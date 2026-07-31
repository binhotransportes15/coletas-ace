@echo off
cd /d "%~dp0"
title ACE Console CMD
chcp 65001 >nul
REM Exemplos:
REM   ace.bat
REM   ace.bat /automatica
REM   ace.bat /automatica 5m
REM   ace.bat /status
REM   ace.bat /push
REM   ace.bat /pull
python ace_cmd.py %*
if errorlevel 1 pause
