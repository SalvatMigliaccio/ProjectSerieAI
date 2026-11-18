@echo off
REM  Queue a pipeline command instead of running it here:
REM    accoda.cmd predict_round      accoda.cmd close_round
REM  The worker runs it, one job at a time, and the admin panel shows it.
REM  This is what the scheduled tasks should call once the worker is running.
setlocal
set "REPO=%~dp0..\.."
pushd "%REPO%"
"%REPO%\.venv\Scripts\python.exe" -m backend.worker --accoda %1
set "CODE=%ERRORLEVEL%"
popd
exit /b %CODE%
