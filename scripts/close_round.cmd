@echo off
REM Tuesday task: takes the round from played to closed.
REM Refuses to close an incomplete round on its own, so running it on a week
REM with a postponed match is a no-op that says so and exits 0.
"%~dp0run_task.cmd" close_round
exit /b %ERRORLEVEL%
