@echo off
REM Ferma worker, sito e Docker. Vedi ferma_app.ps1 (-LasciaDocker per tenerlo acceso).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ferma_app.ps1" %*
pause
