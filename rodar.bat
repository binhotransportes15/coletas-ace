@echo off
cd /d "%~dp0"
set QT_OPENGL=software
set QSG_RHI_BACKEND=software
python app.py
if errorlevel 1 pause
