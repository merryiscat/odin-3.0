@echo off
setlocal
REM ==========================================================================
REM Odin 3.0 - trading model #1 (rule-trade-v1), one run per trading day, before the open.
REM   Reads today's pick list + yesterday's market state from Supabase, writes order intents
REM   and virtual fills to model_module\records\rule-trade-v1.sqlite. Never places real orders.
REM Schedule: weekdays 08:30 (after the list night batch, before Odin3-realtime-day at 08:50).
REM   schtasks /Create /TN Odin3-trade /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 08:30 ^
REM     /TR "\"%~dp0trade-day.cmd\"" /F
REM Holidays/duplicates are recorded by the script itself (status holiday / skipped_duplicate).
REM ==========================================================================

pushd "%~dp0.."

if not exist logs mkdir logs
echo [%date% %time%] trade-day start >> logs\trade-day.log
uv run --project model_module python model_module/run_trade.py >> logs\trade-day.log 2>&1
set ERR=%errorlevel%
echo [%date% %time%] trade-day end (exit %ERR%) >> logs\trade-day.log

popd
endlocal & exit /b %ERR%
