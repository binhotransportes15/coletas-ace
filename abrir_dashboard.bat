@echo off
cd /d "%~dp0"
REM Abre o dashboard local (file://). Preferivel: no CRT use a aba Local.
start "" "%~dp0dashboard\index.html"
