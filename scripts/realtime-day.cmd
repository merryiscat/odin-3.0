@echo off
setlocal
REM ==========================================================================
REM Odin 3.0 - one trading day of the realtime state discriminator.
REM   1) refresh leader basket (dim_basket)
REM   2) start the KIS 1-minute collector in its own window
REM   3) run the minute-by-minute judge here until 15:30
REM Start before 09:00 (e.g. Task Scheduler at 08:50 on weekdays). Needs .env (see .env.example).
REM ==========================================================================

pushd "%~dp0.."

echo [1/3] leader basket
uv run --project data_pipeline python data_pipeline/build_basket.py
if errorlevel 1 goto :fail

echo [2/3] collector window
start "odin3 collector" cmd /k uv run --project data_pipeline python data_pipeline/collect_rt.py

echo [3/3] realtime judge
uv run --project state_module python state_module/run_rt.py

popd
endlocal
exit /b 0

:fail
echo [ERROR] basket refresh failed - collector/judge not started.
popd
endlocal
exit /b 1
