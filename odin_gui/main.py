"""오딘 3.0 GUI — 앱 창 실행 (첫 버전)

시장 온도 화면(app/market-temp.html)을 윈도우 내장 웹뷰 창에 띄운다.
화면이 3.0 DB를 공개 읽기 키로 직접 읽는다(국내 탭: 장중엔 1분 실시간 판정, 그 외엔 종가 판정 — 1분마다 다시 읽음).

나중에 할 일:
- 화면이 여러 장이 되면 창 안에서 화면 전환(사이드바)을 단다.
- 다 되면 PyInstaller로 .exe로 포장한다(사용자가 설치하는 앱).

실행법: 이 폴더(odin_gui)에서  uv run main.py
"""

from pathlib import Path

import webview

# 띄울 화면 파일 — 이 폴더 기준 상대 경로라 어디서 실행해도 안전
HTML = Path(__file__).parent / "app" / "market-temp.html"


def main() -> None:
    webview.create_window(
        title="Odin 3.0 — 시장 온도",
        url=HTML.as_uri(),      # 파일 경로를 창에 그대로 로드
        width=1240,
        height=860,
        min_size=(760, 560),    # 너무 작게 줄이면 카드가 깨져서 최소 크기 고정
    )
    webview.start()             # 창이 닫힐 때까지 여기서 멈춰 있는다


if __name__ == "__main__":
    main()
