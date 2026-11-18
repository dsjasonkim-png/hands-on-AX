"""폴더 검색 평가 사례. tests/test_eval_cases.py(가짜 판단기)와 live_check.py(실제 모델)가 함께 쓴다.

- expect: outcome·status·approval_mode·questions 같은 상태 필드. 답 문장은 채점하지 않는다.
- expect_decision: 핵심 판단인 '답의 주된 근거 파일(primary_file)'을 채점한다.
- FS-007~010은 두 번째 뇌 정리 요청이다. PPT·엑셀을 markitdown으로 읽으므로 markitdown이 설치돼 있어야 한다.
  사례는 같은 그래프에서 순서대로 돌므로 FS-010은 FS-007이 만든 노트를 덮어쓰는 경우가 된다.
업무 규칙에 차단(block)이 없으므로 차단 사례는 두지 않는다.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from axcore import Ledger, build_graph

from jobs import without_reasoning

from .spec import make_spec


def req(rid, question):
    return {"request_id": rid, "payload": {"question": question}}


def org(rid, path):
    return {"request_id": rid, "payload": {"organize": path}}


CASES = [
    # 정상: 보고서와 ERP 추출본의 값이 같다 → 자동 기록
    {"case_id": "FS-001", "trigger": req("FS-001", "부산 3분기 매출 얼마야?"), "replies": [],
     "expect": {"outcome": "committed", "status": "completed", "approval_mode": "auto"},
     "expect_decision": {"primary_file": "매출보고서_3분기.txt"}},
    # 정상: 키워드가 아니라 내용으로 찾아 읽어야 하는 질문
    {"case_id": "FS-002", "trigger": req("FS-002", "새벽배송 시범사업을 중단한 이유가 뭐야?"), "replies": [],
     "expect": {"outcome": "committed", "status": "completed", "approval_mode": "auto"},
     "expect_decision": {"primary_file": "프로젝트회고_새벽배송.md"}},
    # 정보 부족: 회의록이 두 개라 '그 회의'를 되묻는다
    {"case_id": "FS-003", "trigger": req("FS-003", "그 회의에서 정한 마감일 알려줘"), "replies": ["물류 회의"],
     "expect": {"outcome": "committed", "status": "completed", "questions": 1},
     "expect_decision": {"primary_file": "회의록_물류_1020.md"}},
    # 실패: 폴더에 없는 내용. 지어내지 않고 failed
    {"case_id": "FS-004", "trigger": req("FS-004", "인사팀 연봉 테이블 알려줘"), "replies": [],
     "expect": {"outcome": "failed", "status": "failed"}},
    # 입력 오류: 빈 질문은 입력 로더가 끊는다(LLM 호출 없음)
    {"case_id": "FS-005", "trigger": req("FS-005", "   "), "replies": [],
     "expect": {"outcome": "failed"}},
    # 모델이 틀리기 쉬운 경우: 보고서(650)와 ERP 확정치(610)가 다르다. 한 파일만 보고 completed로 답하기 쉽다
    {"case_id": "FS-006", "trigger": req("FS-006", "대구 3분기 매출 얼마야?"),
     "replies": [{"action": "approve", "reviewer": "재무팀 이OO", "comment": "ERP 확정치 기준으로 안내"}],
     "expect": {"outcome": "committed", "status": "needs_review", "approval_mode": "human"}},
    # 정리: PPT를 마크다운으로 바꿔 새 노트로 → 자동 저장
    {"case_id": "FS-007", "trigger": org("FS-007", "전략발표_2027.pptx"), "replies": [],
     "expect": {"outcome": "committed", "status": "completed", "approval_mode": "auto"},
     "expect_decision": {"primary_file": "전략발표_2027.pptx"}},
    # 정리: 시트가 두 개인 엑셀 → 자동 저장
    {"case_id": "FS-008", "trigger": org("FS-008", "반품현황_10월.xlsx"), "replies": [],
     "expect": {"outcome": "committed", "status": "completed", "approval_mode": "auto"},
     "expect_decision": {"primary_file": "반품현황_10월.xlsx"}},
    # 입력 오류: 폴더에 없는 파일 정리 요청은 입력 로더가 끊는다(LLM 호출 없음)
    {"case_id": "FS-009", "trigger": org("FS-009", "없는파일.pptx"), "replies": [],
     "expect": {"outcome": "failed"}},
    # 사람 확인: 이미 있는 노트(FS-007)를 다시 정리하면 덮어쓰기 전에 사람이 본다
    {"case_id": "FS-010", "trigger": org("FS-010", "전략발표_2027.pptx"),
     "replies": [{"action": "approve", "reviewer": "경영기획팀 박OO", "comment": "최신 발표본으로 갱신"}],
     "expect": {"outcome": "committed", "status": "completed", "approval_mode": "human"}},
]


def make_graph(model=None, runtime: str | Path | None = None, embeddings=None, index_path=None):
    """embeddings를 주면 semantic_search를 켠다. live_check.py는 넘기지 않으므로 공식 평가는 키워드 검색 + 읽기다."""
    runtime = Path(runtime) if runtime else Path(tempfile.mkdtemp(prefix="folder_search-"))
    return build_graph(make_spec(answers_path=runtime / "answers.json", brain_dir=runtime / "brain", model=without_reasoning(model),
                                 embeddings=embeddings, index_path=index_path), Ledger(runtime / "ledger.json"))
