@echo off
cd /d "%~dp0"
title ACE Console CMD
chcp 65001 >nul
REM Exemplos:
REM   ace.bat
REM   ace.bat /automatica
REM   ace.bat /site off
REM   ace.bat /site off Manutencao ate 14h
REM   ace.bat /site on
REM   ace.bat /interromper
REM   ace.bat /ligar
REM   ace.bat /push
python ace_cmd.py %*
if errorlevel 1 pause
