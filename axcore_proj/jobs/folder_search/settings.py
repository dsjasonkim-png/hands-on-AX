"""시연(run.py --live)에서 .env를 읽어 모델과 임베딩을 고른다. live_check.py 공식 평가는 이 설정을 쓰지 않는다.

.env 위치: axcore_proj의 상위 폴더 .env → 없으면 그 상위 폴더 .env (live_check.py와 같은 순서)
  OPENAI_API_KEY      실제 모델과 의미 검색 임베딩에 쓴다
  LLM=<공급자:모델>    init_chat_model 문자열로 모델을 바꾼다 (없으면 jobs/__init__.py의 DEFAULT_MODEL)
  SEMANTIC=0          의미 검색(semantic_search)을 끈다
API 키는 출력하지 않는다.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from jobs import DEFAULT_MODEL

SKELETON = Path(__file__).resolve().parents[3]          # axcore_proj의 상위 폴더
INDEX_PATH = Path(__file__).resolve().parents[2] / "samples" / "folder_search" / "files" / ".index.json"


def load_env() -> Path | None:
    """이미 있는 환경변수는 덮어쓰지 않는다."""
    for folder in (SKELETON, SKELETON.parent):
        env = folder / ".env"
        if not env.exists():
            continue
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.split(" #")[0].strip().strip('"').strip("'")   # 값 뒤 주석 허용
            os.environ.setdefault(key.strip(), value)
        return env
    return None


def make_llm(choice: str | None = None) -> tuple[Any, str]:
    """(모델 객체, 표시 이름). choice가 없으면 .env의 LLM, 그것도 없으면 DEFAULT_MODEL."""
    choice = choice or os.getenv("LLM") or DEFAULT_MODEL
    from langchain.chat_models import init_chat_model
    return init_chat_model(choice, temperature=0), choice


def make_embeddings() -> Any | None:
    """OPENAI_API_KEY가 있으면 OpenAI 임베딩(text-embedding-3-small), 없거나 SEMANTIC=0이면 None(의미 검색 끔)."""
    if os.getenv("SEMANTIC") == "0" or not os.getenv("OPENAI_API_KEY"):
        return None
    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(model="text-embedding-3-small")
