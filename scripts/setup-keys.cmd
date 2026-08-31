@echo off
setlocal
REM ==========================================================================
REM Odin 3.0 API key setup wizard - launcher for PowerShell / CMD users.
REM Runs the bash wizard (scripts/setup-keys.sh) through Git Bash.
REM Usage (from the odin_3.0 folder):   .\scripts\setup-keys.cmd
REM ==========================================================================

set "BASH=C:\Program Files\Git\bin\bash.exe"
if not exist "%BASH%" set "BASH=C:\Program Files (x86)\Git\bin\bash.exe"
if not exist "%BASH%" (
  echo.
  echo [ERROR] Git Bash not found.
  echo Install Git for Windows, then run again: https://git-scm.com/download/win
  echo.
  pause
  exit /b 1
)

REM Move to the project root ( ..\ from this scripts\ folder ) so .env lands there.
pushd "%~dp0.."
"%BASH%" scripts/setup-keys.sh
popd

endlocal
