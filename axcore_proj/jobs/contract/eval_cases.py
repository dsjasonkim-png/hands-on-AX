"""계약서 검토 평가 사례. tests/test_eval_cases.py(가짜 판단기)와 live_check.py(실제 모델)가 함께 쓴다."""
from __future__ import annotations

import tempfile
from pathlib import Path

from axcore import Ledger, build_graph

from .playbook import VERSION
from .run import CASES as DEMO_CASES
from .spec import make_spec

EXTRA = Path(__file__).resolve().parents[2] / "samples" / "contract"   # 18장에서 더한 샘플

EXPECT = {
    "CT-001": {"outcome": "committed", "status": "needs_review"},
    "CT-002": {"outcome": "committed", "status": "needs_review"},
}

# 플레이북 기준을 모두 충족하는 계약서. 문제가 없어도 담당자 확인을 거친다(승인 정책이 항상 human).
# 처음 문안은 PB-04의 '고객정보·영업비밀' 범위가 빠져 있었다. 실제 모델이 이를 짚어 문안을 기준에 맞췄다(18장).
COMPLIANT = ("표준 물류 계약서\n제1조 (대금 지급) 갑은 검수 완료 후 30일 이내에 대금을 지급한다.\n"
             "제2조 (손해배상) 을의 배상 한도는 연간 계약금액 이상으로 하되, 고의 또는 중대한 과실에는 한도를 두지 않는다.\n"
             "제3조 (해지) 각 당사자는 30일 전 서면 통지로 해지할 수 있다.\n"
             "제4조 (비밀유지) 을은 갑의 고객정보와 영업비밀을 비밀로 유지하며, 이 의무는 계약 종료 후 3년간 존속한다.\n"
             "제5조 (관할) 서울중앙지방법원을 관할 법원으로 한다.")

CASES = [{"case_id": rid, "trigger": {"request_id": rid, "payload": payload},
          "replies": list(replies), "expect": EXPECT[rid]}
         for rid, payload, replies in DEMO_CASES] + [
    {"case_id": "CT-003", "trigger": {"request_id": "CT-003", "payload": {"text": COMPLIANT}},
     "replies": [{"action": "approve", "reviewer": "법무팀 정OO"}],
     "expect": {"outcome": "committed", "status": "completed", "approval_mode": "human"}},
    {"case_id": "CT-004", "trigger": {"request_id": "CT-004", "payload": {"text": "조항이 없는 메모입니다. 다음 주 회의에서 논의합시다."}},
     "replies": [], "expect": {"outcome": "failed"}},   # 입력 로더가 조항 없음으로 끊는다(LLM 호출 없음)
    # 3쪽 스캔본에서 2쪽(제4·5조)이 빠짐. 빠진 범위의 기준은 '누락'이 아니라 '미확인'으로 남기고 사람이 재스캔을 요청한다
    {"case_id": "CT-005", "trigger": {"request_id": "CT-005",
                                      "payload": {"image": str(EXTRA / "logistics_contract_scan_partial.png")}},
     "replies": [{"action": "reject", "comment": "빠진 2쪽(제4·5조)을 다시 스캔해 제출해 주세요"}],
     "expect": {"outcome": "rejected", "status": "needs_review"}},
    # 요청서에 이전 플레이북 버전(2026.03)이 적혀 있어도 현재 버전으로 검토하고, 결과에 현재 버전을 남긴다
    {"case_id": "CT-006", "trigger": {"request_id": "CT-006", "payload": {"text": COMPLIANT, "playbook_version": "2026.03"}},
     "replies": [{"action": "approve", "reviewer": "법무팀 정OO"}],
     "expect": {"outcome": "committed", "status": "completed", "approval_mode": "human"},
     "expect_decision": {"playbook_version": VERSION}},
]


def make_graph(model=None, runtime: str | Path | None = None):
    runtime = Path(runtime) if runtime else Path(tempfile.mkdtemp(prefix="contract-"))
    return build_graph(make_spec(review_dir=runtime / "reviews", model=model), Ledger(runtime / "ledger.json"))
