@echo off
cd /d "%~dp0"
chcp 65001 >nul
REM ================================
REM  BINHO · ACE
REM  Padrao: so o painel CRT (sem janela preta)
REM  ace.bat           → painel
REM  ace.bat cmd       → console texto (legado)
REM  ace.bat automatica → loop sem janela (via CRT se aberto; senao headless)
REM ================================

if /I "%~1"=="cmd" (
  title BINHO · ACE Console
  shift
  python -u ace_cmd.py %*
  if errorlevel 1 pause
  goto :eof
)

if /I "%~1"=="console" (
  title BINHO · ACE Console
  shift
  python -u ace_cmd.py %*
  if errorlevel 1 pause
  goto :eof
)

REM Sem argumentos ou "crt" / "painel" → so o painel grafico
if "%~1"=="" goto :crt
if /I "%~1"=="crt" goto :crt
if /I "%~1"=="painel" goto :crt
if /I "%~1"=="gestao" goto :crt

REM Comandos CLI ainda aceitos (automatica, 50, sync...) sem abrir menu
python -u ace_cmd.py %*
if errorlevel 1 pause
goto :eof

:crt
REM Reinicia o painel: fecha instancia antiga para carregar o codigo atual
python -c "from crt_bridge import kill_existing_crt; kill_existing_crt()" 2>nul
pythonw -u ace_crt.py 2>nul
if errorlevel 1 (
  REM Fallback se pythonw nao existir
  python -u ace_crt.py
  if errorlevel 1 pause
)
goto :eof
