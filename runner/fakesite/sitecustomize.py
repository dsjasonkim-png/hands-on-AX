"""가짜 모델 모드에서 sh()로 띄운 파이썬 스크립트도 가짜 모델을 쓰게 한다."""
import os
import sys

if os.environ.get("BOOK_RUNNER_FAKE") == "1":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        import fakechat
        fakechat.install()
    except Exception as e:  # 패키지가 없으면 그냥 넘어간다
        print("[가짜 모델 설치 실패]", e, file=sys.stderr)
