@echo off
REM Weekend task: the GBM's opinion on the upcoming round, saved for Monday.
REM
REM NOT PRODUCTION, and the scheduler must not make it look like it is: this
REM writes only in experiments\output, never in track_record\. `run_task.cmd`
REM knows it and skips the last_run.json entry, so /api/status keeps speaking
REM only about what actually touched the registry.
REM
REM IT RUNS AFTER predict_round, ON PURPOSE. It has no ingestion of its own: it
REM reads the dataset and the odds that predict_round has just rebuilt. Alone,
REM the first run of the weekend would train on stale data.
REM
REM Idempotent: the saved file is merged, not overwritten, and on equal matches
REM the older row wins — so a Saturday run cannot erase Friday's prediction for
REM a match already played.
"%~dp0run_task.cmd" predict_m5
exit /b %ERRORLEVEL%
