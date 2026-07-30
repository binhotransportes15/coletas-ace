@echo off
cd /d "%~dp0"
title ACE /automatica 50+103
chcp 65001 >nul
echo.
echo  ACE /automatica · intervalo = config loop_intervalo (padrao 5m)
echo  Defina com: ace.bat  e  /e intervalo 30s
echo  Ctrl+C para parar
echo.
python ace_cmd.py /automatica %*
if errorlevel 1 pause
