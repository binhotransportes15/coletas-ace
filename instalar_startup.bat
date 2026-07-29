@echo off
setlocal
cd /d "%~dp0"
set "ACE_DIR=%cd%"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT=%STARTUP%\ACE_Robot.bat"

where python >nul 2>&1
if errorlevel 1 (
  echo Python nao encontrado no PATH.
  pause
  exit /b 1
)

(
  echo @echo off
  echo cd /d "%ACE_DIR%"
  echo if defined LOCALAPPDATA set PLAYWRIGHT_BROWSERS_PATH=%%LOCALAPPDATA%%\ms-playwright
  echo python ace_robot.py ^>^> "data\logs\startup_console.log" 2^>^&1
) > "%SHORTCUT%"

echo.
echo Atalho criado em:
echo %SHORTCUT%
echo.
echo Robo ACE na pasta:
echo %ACE_DIR%
echo.
echo Teste agora: python ace_robot.py
echo.
pause
