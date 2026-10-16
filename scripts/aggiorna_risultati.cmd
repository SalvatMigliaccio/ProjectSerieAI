@echo off
REM Evening task: the scores of the matches just played, so the dashboard shows
REM them the same night (as PROVISIONAL) instead of waiting for close_round.
REM NOT a production write: it never touches track_record\, and run_task.cmd
REM keeps it out of last_run.json for the same reason it keeps M5 out.
REM About 30 seconds: Understat and football-data only, no FBref browser.
REM Idempotent: running it twice just finds the same scores twice.
"%~dp0run_task.cmd" aggiorna_risultati
exit /b %ERRORLEVEL%
