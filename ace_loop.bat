@echo off
cd /d "%~dp0"
title ACE /automatica 50+103
chcp 65001 >nul
echo.
echo  ACE /automatica · 50 (D-1 / seg=sex-sab) + 103 (HOJE) a cada 5 min
echo  Ctrl+C para parar
echo.
python ace_cmd.py /automatica 5
if errorlevel 1 pause
