@echo off
REM Avvia Docker, il sito e il worker, poi apre il browser. Vedi avvia_app.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0avvia_app.ps1" %*
if errorlevel 1 pause
