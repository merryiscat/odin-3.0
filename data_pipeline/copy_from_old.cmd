@echo off
rem 옛 오딘 DB -> 3.0 DB 밤 복사 (더블클릭 실행용)
rem 먼저 이 폴더의 .env에 주소/키 4개를 채워야 합니다 (.env.example 참고)
cd /d "%~dp0"
uv run copy_from_old.py
pause
