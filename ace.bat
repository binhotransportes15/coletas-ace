@echo off
cd /d "%~dp0"
title ACE Console CMD · OPERACIONAL
chcp 65001 >nul
REM Exemplos:
REM   ace.bat
REM   ace.bat automatica
REM   ace.bat /automatica 5m
REM   ace.bat viz on
REM   ace.bat viz off
REM   ace.bat 78
REM Observacao: no PowerShell prefira "ace.bat viz on" (sem barra) —
REM   "./ace.bat /viz on" às vezes come o argumento.
python -u ace_cmd.py %*
if errorlevel 1 pause
