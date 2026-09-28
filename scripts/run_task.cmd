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
    echo usage: run_task.cmd ^<predict_round^|close_round^|predict_m5^> [args]
    exit /b 2
)
shift

REM  Which module the task name maps to, and whether the run is worth recording
REM  in last_run.json. Only the two PRODUCTION commands are: /api/status speaks
REM  about the track record, and M5 does not write to it — an entry there would
REM  suggest the registry had been touched. `last_run.py` would refuse it
REM  anyway, its --command has a fixed list of choices.
REM
REM  THE PREFIX IS `goalmodel.prediction.`, NOT `src.`. The package moved to
REM  src/goalmodel/ with a sub-package per layer, and `src.predict_round` no
REM  longer exists. This file was not updated with the rest, and the failure
REM  was quiet in the worst way: the scheduled run would die with "No module
REM  named 'src'", last_run.py would never be reached, and /api/status would
REM  simply keep saying the scheduler had not run — the same silent failure
REM  the popd comment below describes, from the other end.
set "MODULE=goalmodel.prediction.%TASK%"
set "RECORD=1"
if /i "%TASK%"=="predict_m5" (
    set "MODULE=goalmodel.experiments.predici_gbm"
    set "RECORD="
)

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

REM  SRC-LAYOUT: `goalmodel` sits under src/ and is importable only once the
REM  package is installed in the venv. Running from the repo root is no longer
REM  enough, which is the one thing about the new layout that bites a machine
REM  that used to work. Checked here so the log says what to run instead of
REM  holding a ModuleNotFoundError traceback nobody reads on a Friday evening.
REM
REM  IT IMPORTS THE TASK'S OWN MODULE, NOT `goalmodel`. Since 26 September 2026
REM  the base dependencies are only what `import goalmodel` needs: ingestion
REM  moved to the `ingest` extra and LightGBM to `ml`. `import goalmodel` would
REM  therefore succeed on a venv that cannot run either task, and the run would
REM  die further in — past the check that exists to keep that from happening.
REM
REM  `goalmodel.ingest` IS CHECKED SEPARATELY, and that is not belt and braces.
REM  Importing predict_round does NOT pull soccerdata: the ingestion stages are
REM  imported inside the functions that use them, so a venv without the
REM  `ingest` extra passes the first check and fails minutes later, on the
REM  network step, where the log reads like the site was down. Only the two
REM  production tasks need it — predict_m5 reads what is already on disk — so
REM  it is checked only when RECORD is set, which is exactly those two.
"%PY%" -c "import %MODULE%" 2>nul
if errorlevel 1 (
    echo [ERROR] %MODULE% non importabile nel venv. Rimedio: >> "%LOG%"
    echo         %PY% -m pip install -r requirements.lock >> "%LOG%"
    echo         %PY% -m pip install -e ".[all]" --no-deps >> "%LOG%"
    popd
    exit /b 3
)
if defined RECORD (
    "%PY%" -c "import goalmodel.ingest" 2>nul
    if errorlevel 1 (
        echo [ERROR] extra `ingest` mancante: %TASK% scarica dati. Rimedio: >> "%LOG%"
        echo         %PY% -m pip install -e ".[all]" --no-deps >> "%LOG%"
        popd
        exit /b 3
    )
)

"%PY%" -m %MODULE% %1 %2 %3 >> "%LOG%" 2>&1
set "CODE=%ERRORLEVEL%"

echo [END] %TASK% exit=%CODE% >> "%LOG%"
if defined RECORD "%PY%" -m backend.api.last_run --command %TASK% --exit-code %CODE% --log "scheduler_%DAY%.log" --started-at "%STARTED%" >> "%LOG%" 2>&1
popd

exit /b %CODE%
