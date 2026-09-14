@echo off
setlocal
REM ==========================================================================
REM Odin 3.0 - after the close: copy old Odin DB (index daily + minute) then write
REM the end-of-day state line (basis=eod) that the GUI S1 screen reads.
REM Run in the evening, after old Odin has saved today's index daily candle.
REM ==========================================================================

pushd "%~dp0.."

echo [1/2] copy from old Odin DB
pushd data_pipeline
uv run copy_from_old.py
set COPY_ERR=%errorlevel%
popd
if not "%COPY_ERR%"=="0" goto :fail

echo [2/2] end-of-day judgment
uv run --project state_module python state_module/run_eod.py
if errorlevel 1 goto :fail

popd
endlocal
exit /b 0

:fail
echo [ERROR] eod step failed - see messages above.
popd
endlocal
exit /b 1
