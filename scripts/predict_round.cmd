@echo off
REM Friday task: takes the round from open to predicted.
REM --no-open: nobody is in front of the screen, and a browser window opened by
REM a scheduled task on a locked session is a window nobody closes.
REM Idempotent: re-running it skips matches already in the log.
REM
REM `call` IS NOT OPTIONAL. Without it cmd hands control to run_task.cmd
REM and never comes back: the lines below never ran, and M5 was silently
REM skipped on every scheduled Friday (found 23 October 2026, round 8).
call "%~dp0run_task.cmd" predict_round --no-open
set "CODE=%ERRORLEVEL%"

REM  Then the GBM's second opinion on the same round, saved for Monday.
REM
REM  CHAINED HERE AND NOT SCHEDULED ON ITS OWN, for two reasons. Order: M5 does
REM  no ingestion, it reads the dataset predict_round has just rebuilt.
REM  Coverage: every trigger that fires predict_round — including the ones
REM  Windows runs late, after the machine wakes — covers M5 too, with no second
REM  set of times to keep aligned.
REM
REM  ITS EXIT CODE IS DISCARDED, ON PURPOSE. M5 is diagnostics: if the GBM
REM  fails to train, the registry has still been written, and the task must not
REM  report a failed prediction round for something that never touches it.
call "%~dp0predict_m5.cmd"

exit /b %CODE%
