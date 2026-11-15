"""AX 공통 골격의 계약: 트리거, 근거, 판단 결과, 직무 정의(JobSpec)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

Status = Literal["completed", "needs_info", "needs_review", "failed"]
ApprovalMode = Literal["auto", "human", "block"]


class Trigger(BaseModel):
    """업무를 시작시키는 입력. 사람의 요청, 이벤트, 정기 배치를 같은 형식으로 받는다."""

    kind: Literal["request", "event", "batch"] = "request"
    payload: dict[str, Any]
    request_id: str | None = None  # 없으면 payload로 결정적 ID를 만든다

    def resolved_id(self) -> str:
        if self.request_id:
            return self.request_id
        raw = json.dumps({"kind": self.kind, "payload": self.payload}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


class Evidence(BaseModel):
    """판단의 근거 한 건. 검증기가 원본을 다시 열어 같은 내용을 찾을 수 있도록 어디서 무엇을 봤는지 적는다."""

    source: str = Field(description="근거를 가져온 곳. 입력으로 받은 신청서라면 'request', "
                                    "규정이라면 규정 ID(예: SUP-03), 문서라면 문서 이름이나 파일 경로")
    locator: str = Field(description="그곳 안에서 근거가 있는 자리. 신청서라면 필드 이름(예: quantity), "
                                     "문서라면 조항 번호나 줄 번호")
    quote: str = Field(default="", description="그 자리에 적힌 원문이나 값을 고치지 않고 그대로 옮긴 것(예: 20)")


class AXDecision(BaseModel):
    """모든 직무가 공유하는 판단 결과. 직무별 결과 형식은 이 클래스를 상속해 필드를 더한다."""

    status: Status = Field(description="completed: 처리 가능, needs_info: 정보 부족, "
                                       "needs_review: 사람 확인 필요, failed: 처리 불가")
    summary: str = Field(description="판단 요약")
    evidence: list[Evidence] = Field(default_factory=list)
    question: str | None = Field(default=None, description="needs_info일 때 사용자에게 물을 질문")


@dataclass
class DecideContext:
    """판단 단계에 전달하는 입력. 재질문 답변과 검증·검토 피드백이 누적된다."""

    job_input: dict[str, Any]
    clarifications: list[dict[str, str]]
    feedback: list[str]


@dataclass
class JobSpec:
    """직무마다 바꾸는 일곱 가지 교체 지점."""

    name: str
    decision_model: type[AXDecision]                           # 결과 형식 (AXDecision 상속)
    load_input: Callable[[Trigger], dict[str, Any]]            # 1. 입력 로더 (형식 오류는 예외)
    decide: Callable[[DecideContext], AXDecision]             # 2+3+4. 도구·지침·결과 형식을 가진 판단 단계
    verify: Callable[[AXDecision, dict[str, Any]], list[str]]  # 5. 검증기: 오류 메시지 목록
    approval_policy: Callable[[AXDecision, dict[str, Any]], ApprovalMode] = lambda d, i: "human"  # 6. 승인 정책
    commit: Callable[[AXDecision, dict[str, Any], str], str] | None = None  # 7. 반영 어댑터 (반환: 반영 참조)
    require_evidence: bool = True
    max_model_errors: int = 3    # 한 판단 단계 안에서 허용하는 모델 오류(검증 실패·예외) 횟수. 검증을 통과하면 초기화
    max_questions: int = 2       # 정보 보완 질문 상한
    extra: dict[str, Any] = field(default_factory=dict)
