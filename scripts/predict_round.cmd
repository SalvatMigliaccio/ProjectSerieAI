@echo off
REM Friday task: takes the round from open to predicted.
REM --no-open: nobody is in front of the screen, and a browser window opened by
REM a scheduled task on a locked session is a window nobody closes.
REM Idempotent: re-running it skips matches already in the log.
"%~dp0run_task.cmd" predict_round --no-open
exit /b %ERRORLEVEL%
