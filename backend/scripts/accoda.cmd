@echo off
REM ---------------------------------------------------------------------------
REM  What the scheduled tasks call (ADR 0007, phase 3):
REM    accoda.cmd predict_round      accoda.cmd close_round
REM
REM  Hands the command to the pipeline worker through the queue, so a
REM  scheduled run and a run launched from the admin panel never overlap, and
REM  the panel shows both.
REM
REM  IF NOBODY WOULD RUN IT, IT RUNS HERE. `--accoda` exits 10 when the
REM  database does not answer (Docker off) or no worker polled recently. Then
REM  this script falls back to run_task.cmd, the old direct run: a Friday
REM  prediction must never depend on Docker being up. With the database down
REM  the worker cannot run anything either, so the two cannot overlap.
REM ---------------------------------------------------------------------------
setlocal
set "REPO=%~dp0..\.."
set "TASK=%~1"
pushd "%REPO%"
if not exist "%REPO%\logs" mkdir "%REPO%\logs"
"%REPO%\.venv\Scripts\python.exe" -m backend.worker --accoda %TASK% >> "%REPO%\logs\accoda.log" 2>&1
set "CODE=%ERRORLEVEL%"
popd
if "%CODE%"=="10" (
    if /i "%TASK%"=="predict_round" (
        call "%~dp0run_task.cmd" predict_round --no-open
    ) else (
        call "%~dp0run_task.cmd" %TASK%
    )
    exit /b %ERRORLEVEL%
)
exit /b %CODE%
