@echo off
REM ---------------------------------------------------------------------------
REM  The pipeline worker (ADR 0007, phase 3): runs the predict_round and
REM  close_round requests queued from the admin panel of the site.
REM
REM  Meant for a Task Scheduler task "At log on" (see docs/COMANDI.md): it
REM  polls the database every 30 seconds and never exits by itself. If the PC
REM  is off the requests wait in the queue, and the panel shows the worker
REM  offline. Output: logs\worker_YYYYMMDD.log, plus the tail in the panel.
REM ---------------------------------------------------------------------------
setlocal
set "REPO=%~dp0..\.."
set "PY=%REPO%\.venv\Scripts\python.exe"
pushd "%REPO%"
"%PY%" -m backend.worker %*
set "CODE=%ERRORLEVEL%"
popd
exit /b %CODE%
