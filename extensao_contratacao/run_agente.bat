@echo off
cd /d "%~dp0"
title ACE Agente Contratação
REM Sobe um nivel para achar o ACE (imports + assets)
cd /d "%~dp0.."
python -u -m extensao_contratacao.agent_main %*
if errorlevel 1 pause
