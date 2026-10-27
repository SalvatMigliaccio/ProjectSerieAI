@echo off
REM Friday task: takes the round from open to predicted.
REM --no-open: nobody is in front of the screen, and a browser window opened by
REM a scheduled task on a locked session is a window nobody closes.
REM Idempotent: re-running it skips matches already in the log.
REM
REM M5 (the GBM's second opinion) IS NO LONGER CHAINED HERE. Since 27 October
REM 2026 `predict_round` runs it itself, as its last step, in a separate
REM process and never fatally (`rounds.lancia_m5`). Chaining it here as well
REM would train it twice. The reason it used to be here still holds — M5 reads
REM the dataset predict_round has just rebuilt — and is now kept by the
REM command, so a manual `goalmodel predict-round` gets it too, on any OS.
REM predict_m5.cmd stays for running M5 alone.
call "%~dp0run_task.cmd" predict_round --no-open
exit /b %ERRORLEVEL%
