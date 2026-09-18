@echo off
REM ---------------------------------------------------------------------------
REM  Generic runner for the scheduled commands.
REM
REM    run_task.cmd predict_round [extra args...]
REM    run_task.cmd close_round
REM
REM  Appends stdout and stderr to logs\scheduler_YYYYMMDD.log (one file per day,
REM  both commands share it) and then records the outcome in logs\last_run.json,
REM  which is what GET /api/status reads.
REM
REM  The date comes from PowerShell and not from %DATE%: %DATE% follows the
REM  Windows locale, so on an Italian machine it is dd/mm/yyyy and the slashes
REM  would become directory separators in the file name.
REM
REM  Exit code is propagated, so Task Scheduler shows the real outcome in its
REM  "Last Run Result" column instead of a green 0 on a failed run.
REM ---------------------------------------------------------------------------
setlocal

set "REPO=%~dp0.."
set "PY=%REPO%\.venv\Scripts\python.exe"
set "TASK=%~1"
if "%TASK%"=="" (
    echo usage: run_task.cmd ^<predict_round^|close_round^> [args]
    exit /b 2
)
shift

if not exist "%REPO%\logs" mkdir "%REPO%\logs"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "DAY=%%i"
for /f %%i in ('powershell -NoProfile -Command "(Get-Date).ToUniversalTime().ToString(\"yyyy-MM-ddTHH:mm:ssZ\")"') do set "STARTED=%%i"
set "LOG=%REPO%\logs\scheduler_%DAY%.log"

echo. >> "%LOG%"
echo ======================================================================== >> "%LOG%"
echo [%STARTED%] START %TASK% %1 %2 %3 >> "%LOG%"
echo ======================================================================== >> "%LOG%"

REM  BOTH python calls must run from the repo, and the second one is the reason
REM  this is worth a comment. `python -m backend.api.last_run` resolves the
REM  package from the CURRENT directory: with the popd before it, the task ran
REM  from wherever Task Scheduler starts (C:\Windows\system32) and died with
REM  "No module named 'backend'". The prediction itself had already succeeded,
REM  so the only visible symptom was /api/status insisting the scheduler had
REM  never run — a silent failure of exactly the component whose job is to say
REM  whether something failed.
pushd "%REPO%"
"%PY%" -m src.%TASK% %1 %2 %3 >> "%LOG%" 2>&1
set "CODE=%ERRORLEVEL%"

echo [END] %TASK% exit=%CODE% >> "%LOG%"
"%PY%" -m backend.api.last_run --command %TASK% --exit-code %CODE% --log "scheduler_%DAY%.log" --started-at "%STARTED%" >> "%LOG%" 2>&1
popd

exit /b %CODE%
