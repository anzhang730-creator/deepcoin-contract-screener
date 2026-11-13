@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0DeepcoinScreen.exe" (
  start "Deepcoin Screen" "%~dp0DeepcoinScreen.exe"
  exit /b 0
)
if exist "%~dp0launcher.py" (
  where python >nul 2>nul
  if errorlevel 1 (
    echo DeepcoinScreen.exe or Python was not found.
    pause
    exit /b 1
  )
  start "Deepcoin Screen" python "%~dp0launcher.py"
  exit /b 0
)
echo DeepcoinScreen.exe was not found.
pause
exit /b 1
