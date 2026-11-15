"""Z몰 데이터분석팀 매출 변동 원인 분석 — 공통 골격의 교체 지점 7개를 채운 실습.

흐름: 질문 → 분석 기간 정하기(코드) → SQL을 반복 실행하며 고객 여정을 따라 원인 분석(AI)
      → 매출·지표·주원인·채널을 원본에서 다시 계산해 검증(코드)
      → 원인이 분명하면 자동 저장, 여러 요인에 나뉘면 담당자 확인 → 분석 보고서 저장(문장 속 숫자는 코드가 채움)
"""
from __future__ import annotations

import re
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator

from axcore import AXDecision, DecideContext, JobSpec, Trigger
from axcore.llm import agent_decider

from .metrics import (AOV_TOLERANCE, FACTORS, LAST_MONTH_IN_TEXT, METRICS, REQUIRED_TABLES, TOLERANCE,
                      UNIT_AFTER_PLACEHOLDER, Analysis, analyze, connect_ro, fill_numbers, fmt, months_in, months_in_text,
                      pct, prev_month, written_numbers)
from .policies import (AUTO_SHARE, CONCENTRATION_LIFT, CONCENTRATION_SHARE, FLAT_PCT, SQL_HARD_CALLS, SQL_MAX_CALLS,
                       VERSION)
from .readonly_sql import run_readonly_sql

DEFAULT_DB = Path(__file__).resolve().parents[2] / "samples" / "commerce_analysis" / "zmall_orders.db"
DIRECTION_LABEL = {"up": "증가", "down": "감소", "flat": "변화 없음"}

MetricKey = Literal["revenue", "clicks", "product_views", "cart_adds", "checkout_starts", "order_attempts", "paid_orders",
                    "click_to_view", "view_to_cart", "cart_to_checkout", "checkout_to_order", "payment_success_rate",
                    "aov", "discount_rate"]
FactorKey = Literal["clicks", "click_to_view", "view_to_cart", "cart_to_checkout", "checkout_to_order",
                    "payment_success_rate", "aov"]


# ④ 결과 형식 ─────────────────────────────────────────────────────────────
class MetricValue(BaseModel):
    metric: MetricKey = Field(description="지표 키")
    channel: str = Field(default="", description="전체는 빈 문자열, 채널 값이면 web 또는 app")
    base: float = Field(description="비교할 달(base_month)의 값")
    current: float = Field(description="분석할 달(current_month)의 값")


class SalesDecision(AXDecision):
    current_month: str | None = Field(default=None, description="분석할 달 YYYY-MM")
    base_month: str | None = Field(default=None, description="비교할 달 YYYY-MM (분석할 달의 전달)")
    revenue_base: int | None = Field(default=None, description="비교할 달 전체 매출(원)")
    revenue_current: int | None = Field(default=None, description="분석할 달 전체 매출(원)")
    direction: Literal["up", "down", "flat"] | None = Field(default=None, description="매출 변화 방향")
    primary_cause: FactorKey | None = Field(default=None, description="매출 변화에 가장 크게 기여한 요인. flat이면 비움")
    primary_channel: str | None = Field(default=None, description="매출 변화가 몰린 채널(web 또는 app). 없으면 비움")
    metrics: list[MetricValue] = Field(default_factory=list, description="SQL로 확인한 지표 값")
    notes: list[str] = Field(default_factory=list, description="담당자가 추가로 확인할 점")

    @field_validator("current_month", "base_month", "primary_cause", "primary_channel", mode="before")
    @classmethod
    def blank_means_none(cls, v: Any) -> Any:
        """모델이 '비움'을 빈 문자열이나 '없음'으로 적어도 같은 뜻으로 받는다."""
        if isinstance(v, str) and v.strip().lower() in ("", "없음", "none", "null"):
            return None
        return v


# ③ 지침 ──────────────────────────────────────────────────────────────────
METRIC_GUIDE = "\n".join(f"- {k}({m['name']}, {m['unit']}): {m['desc']}" for k, m in METRICS.items())
SKILLS_DIR = Path(__file__).parent / "skills"


def skill_catalog() -> dict[str, str]:
    """skill 이름 → 설명. SKILL.md 머리말에서 읽는다."""
    catalog = {}
    for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        head = path.read_text(encoding="utf-8").split("---")[1]
        catalog[path.parent.name] = re.search(r"^description:\s*(.+)$", head, re.M)[1]
    return catalog


def read_skill(name: str) -> str:
    """skill 본문. 목록에 있는 이름만 읽는다."""
    catalog = skill_catalog()
    if name not in catalog:
        return f"skill 오류: {name}은 없는 skill. 쓸 수 있는 skill: {', '.join(catalog)}"
    return (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].strip()


SKILL_LIST = "\n".join(f"  - {name}: {desc}" for name, desc in skill_catalog().items())
INSTRUCTIONS = f"""너는 Z몰 데이터분석팀의 매출 분석 담당자다. 분석 기준 버전 {VERSION}.
입력으로 질문, 분석 기간, 지표 정의가 주어진다.
- SQL을 짜기 전에 load_skill로 DB의 테이블 구조와 관계를 읽는다. 쓸 수 있는 skill:
{SKILL_LIST}
- [분석 기간]에 분석할 달과 비교할 달이 있으면 current_month와 base_month에 그대로 적고 되묻지 않는다.
  분석할 달이 없으면 추측하지 말고 needs_info로 어느 달을 분석할지 묻는다. 답을 받으면 current_month(YYYY-MM)와
  base_month(그 전달)에 옮겨 적는다.
- 숫자는 run_sql로 SQL을 직접 짜서 확인한다. 추측하거나 어림하지 않는다. 판단 한 번에 {SQL_MAX_CALLS}번까지 부를 수 있다.
  테이블을 합칠 때는 skill의 관계(1:1, 1:N)를 보고 행이 중복되지 않게 한다.
- 고객 여정(클릭 → 상품 조회 → 장바구니 → 주문서 → 주문 시도 → 결제 완료)을 따라 두 달을 비교한다.
  매출 = 클릭 수(clicks) × 클릭→조회율(click_to_view) × 조회→장바구니율(view_to_cart) × 장바구니→주문서율(cart_to_checkout)
  × 주문서→주문율(checkout_to_order) × 결제 성공률(payment_success_rate) × 객단가(aov).
- 확인한 숫자는 metrics에 지표 키, 채널, base(비교할 달 값), current(분석할 달 값)로 적는다. revenue와 일곱 요인의
  전체 값(channel="")은 반드시 적는다. revenue_base·revenue_current에는 두 달의 전체 매출을 적는다.
- direction은 매출 변화가 ±{FLAT_PCT}% 미만이면 flat. 질문의 전제(예: "왜 떨어졌어?")가 데이터와 다르면 direction은
  데이터대로 적고 summary에서 바로잡는다.
- primary_cause에는 일곱 요인 중 기여율이 가장 큰 요인을 적는다. flat이면 비운다.
  요인의 기여율 = ln(분석할 달 값 ÷ 비교할 달 값) ÷ ln(분석할 달 매출 ÷ 비교할 달 매출). 어림하지 말고 run_sql로 계산한다.
- 매출 변화의 {CONCENTRATION_SHARE:.0%} 이상이 한 채널(web 또는 app)에서 나오고 그 비율이 그 채널의 비교할 달 매출 비중보다
  {CONCENTRATION_LIFT}배 이상 크면 primary_channel에 그 채널을 적는다. 그런 채널이 없으면 비운다.
- status는 기여율로만 정한다. 주원인의 기여율이 {AUTO_SHARE:.0%} 이상이면 completed, 미만이면 needs_review.
  확인할 점이 있어도 기여율이 {AUTO_SHARE:.0%} 이상이면 completed로 내고, 확인할 점은 notes에 적는다.
- summary는 세 문장 이내로, 원인이 고객 여정의 어느 단계에서 생겼는지 적는다.
- summary와 notes에는 숫자를 직접 쓰지 않는다. 숫자 자리에는 자리표시를 쓰고, 코드가 원본 값과 단위로 채운다.
  {{지표.base}}는 비교할 달 값, {{지표.current}}는 분석할 달 값, {{지표.change}}는 변화율(부호 없음, % 지표는 차이 %p),
  {{지표.share}}는 기여율이다. 채널 값은 {{web.지표.current}}처럼 채널을 앞에 붙이고, {{web.revenue.share}}는 전체 매출
  변화 중 그 채널의 몫이다. 자리표시 뒤에 단위를 붙이지 않는다. 기준값({AUTO_SHARE:.0%}, {CONCENTRATION_LIFT}배 같은 판단 기준)이나
  100% 같은 숫자도 숫자 없이 '자동 저장 기준', '몰림 기준'처럼 말로 쓴다. 질문에 적힌 숫자도 옮겨 쓰지 않는다(어림한 값일 수
  있다. 바로잡을 때도 자리표시로 쓴다). 날짜(5월, 2026년)는 그대로 써도 된다.
  예: "5월 매출은 4월보다 {{revenue.change}} 줄었다. 주원인은 객단가 하락({{aov.base}} → {{aov.current}}, 기여율 {{aov.share}})이다."
- 피드백에 적힌 원본 값은 코드가 DB에서 다시 계산한 값이다. 네 쿼리 결과와 다르면 피드백 값을 쓴다.
"""


def render(ctx: DecideContext) -> list[dict[str, Any]]:
    """④와 짝을 이루는 입력 표현. 기간·지표 정의를 함께 줘야 지표 키로 답할 수 있다. DB 구조는 load_skill로 읽는다."""
    inp = ctx.job_input
    period = (f"{inp['period_note']} → 분석할 달 {inp['current_month']}, 비교할 달 {inp['base_month']}"
              if inp["current_month"] else f"{inp['period_note']} (DB에 있는 달: {', '.join(inp['months'])})")
    parts = [f"[질문] {inp['question']}", f"[분석 기간] {period}", f"[지표 정의]\n{METRIC_GUIDE}"]
    if ctx.clarifications:
        parts.append("[추가로 받은 정보]\n" + "\n".join(f"- Q: {c['question']} / A: {c['answer']}" for c in ctx.clarifications))
    if inp.get("previous_decision"):
        parts.append("[이전 판단] 아래 피드백에 적힌 곳만 고쳐 전체를 다시 제출한다. 나머지 값은 그대로 둔다.\n"
                     + inp["previous_decision"])
    if ctx.feedback:
        parts.append("[이전 판단에 대한 피드백: 반드시 반영]\n" + "\n".join(f"- {f}" for f in ctx.feedback))
    return [{"role": "user", "content": "\n\n".join(parts)}]


# 보고서: 표의 숫자와 문장 속 숫자를 모두 코드가 원본에서 채운다 ────────────────
JOURNEY = (("클릭", "clicks", None), ("상품 조회", "product_views", "click_to_view"), ("장바구니", "cart_adds", "view_to_cart"),
           ("주문서 진입", "checkout_starts", "cart_to_checkout"), ("주문 시도", "order_attempts", "checkout_to_order"),
           ("결제 완료", "paid_orders", "payment_success_rate"))   # (단계, 건수 지표, 앞 단계에서 넘어온 비율 지표)


def change(b: float, c: float) -> str:
    return f"{(c - b) / b * 100:+.1f}%" if b else "-"


def report_markdown(d: SalesDecision, inp: dict, a: Analysis, request_id: str) -> str:
    b, c, name = a.base, a.current, lambda k: METRICS[k]["name"]
    primary = (f"{name(a.primary)}({a.primary})" if a.primary
               else "없음(변화 없음)" if a.direction == "flat" else "없음(요인으로 나눌 수 없음)")
    lines = [
        f"# 매출 변동 분석 보고서 · {a.current_month} (비교: {a.base_month})", "",
        f"- 요청 ID: {request_id}", f"- 질문: {inp['question']}",
        f"- 분석 기간: {inp['period_note'] if inp['current_month'] else '되물어 받은 답'}",
        f"- 분석 기준: {VERSION}", f"- 판단: {d.status}",
        "", "## 매출", "",
        f"{a.base_month} {fmt('revenue', b['revenue'])} → {a.current_month} {fmt('revenue', c['revenue'])} "
        f"({a.change_pct:+.1f}%, {DIRECTION_LABEL[a.direction]}). "
        f"할인율 {fmt('discount_rate', b['discount_rate'])} → {fmt('discount_rate', c['discount_rate'])}",
        "", "## 고객 여정", "",
        f"| 단계 | {a.base_month} | {a.current_month} | 변화 | 앞 단계에서 넘어온 비율 |", "|---|---|---|---|---|",
        *(f"| {label} | {fmt(k, b[k])} | {fmt(k, c[k])} | {change(b[k], c[k])} | "
          f"{f'{fmt(r, b[r])} → {fmt(r, c[r])}' if r else '-'} |" for label, k, r in JOURNEY),
        "", "## 요인 분해", "",
        f"| 요인 | {a.base_month} | {a.current_month} | 기여율 |", "|---|---|---|---|",
        *(f"| {name(k)} | {fmt(k, b[k])} | {fmt(k, c[k])} | {pct(a.shares[k]) if a.shares else '-'} |" for k in FACTORS),
        "", f"주원인: {primary}",
        "", "## 채널별", "",
        f"| 채널 | {a.base_month} 매출 | {a.current_month} 매출 | 변화 | 전체 변화 중 몫 | {a.base_month} 매출 비중 "
        "| 가장 크게 움직인 요인 |", "|---|---|---|---|---|---|---|",
        *(f"| {r.name} | {fmt('revenue', r.base['revenue'])} | {fmt('revenue', r.current['revenue'])} "
          f"| {change(r.base['revenue'], r.current['revenue'])} "
          f"| {pct(r.share_of_change) if r.share_of_change is not None else '-'} | {pct(r.base_share)} "
          f"| {name(r.top_step) if r.top_step else '-'} |" for r in a.channels),
        "", "| 채널 | " + " | ".join(name(k) for k in FACTORS) + " |", "|---" * (len(FACTORS) + 1) + "|",
        *("| " + r.name + " | " + " | ".join(f"{fmt(k, r.base[k])} → {fmt(k, r.current[k])}" for k in FACTORS) + " |"
          for r in a.channels),
        "", f"변화가 몰린 채널: {', '.join(a.concentrated) or '없음'} "
            f"(전체 변화의 {CONCENTRATION_SHARE:.0%} 이상이고 매출 비중의 {CONCENTRATION_LIFT}배 이상인 채널)",
        "", "## 분석 의견", "", fill_numbers(d.summary, a)[0],
    ]
    if d.notes:
        lines += ["", "## 추가로 확인할 점", "", *(f"- {fill_numbers(n, a)[0]}" for n in d.notes)]
    return "\n".join(lines) + "\n"


def fill_decision(decision: dict, db: str | Path = DEFAULT_DB) -> dict:
    """사람에게 보여 줄 때도 숫자는 코드가 채운다. 승인 요청(pending)의 판단을 넣으면 summary·notes를 채워 돌려준다."""
    d = SalesDecision.model_validate(decision)
    with closing(connect_ro(db)) as conn:
        a = analyze(conn, d.base_month, d.current_month)
    return {**decision, "summary": fill_numbers(d.summary, a)[0], "notes": [fill_numbers(n, a)[0] for n in d.notes]}


def is_date(s: Any) -> bool:
    try:
        return isinstance(s, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", s)) and bool(datetime.strptime(s, "%Y-%m-%d"))
    except ValueError:
        return False


def make_spec(db_path: str | Path = DEFAULT_DB, report_dir: str | Path = "runtime/commerce_analysis/reports",
              decide=None, model: Any = None) -> JobSpec:
    """decide를 주면 그것을, model을 주면 실제 LLM을, 둘 다 없으면 가짜 판단기를 쓴다."""
    db, report_dir = Path(db_path), Path(report_dir)

    # ① 입력 로더: 분석할 달과 DB 형식 확인은 코드의 일 ───────────────────────
    def load_input(trigger: Trigger) -> dict:
        p = trigger.payload
        question, as_of = p.get("question"), p.get("as_of")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("질문(question)이 없음")
        if as_of is not None and not is_date(as_of):
            raise ValueError(f"기준일(as_of)은 YYYY-MM-DD 형식이어야 함: {as_of!r}")
        if not db.exists():
            raise ValueError(f"주문 DB 파일이 없음: {db}")
        with closing(connect_ro(db)) as conn:
            columns = {t: {c[1] for c in conn.execute(f"PRAGMA table_info({t})")} for t in REQUIRED_TABLES}
            missing = [t for t, cols in columns.items() if not cols]
            if missing:
                raise ValueError(f"필수 테이블이 없음: {', '.join(missing)}")
            for t, need in REQUIRED_TABLES.items():
                if need - columns[t]:
                    raise ValueError(f"{t} 테이블에 필수 컬럼이 없음: {', '.join(sorted(need - columns[t]))}")
            months = months_in(conn)
        question = question.strip()
        mentioned = months_in_text(question, months)
        current, note = None, "질문에 분석할 달이 없음"
        if len(mentioned) == 1:                       # 질문에 적은 달이 기준일보다 우선한다
            current, note = mentioned[0], "질문에 적힌 달"
        elif mentioned:
            note = f"질문에 여러 달({', '.join(mentioned)})이 적혀 있어 분석할 달을 정하지 못함"
        elif LAST_MONTH_IN_TEXT.search(question):     # 코드가 기준일로 계산하는 말은 '지난달'뿐이다. 나머지는 묻는다
            if as_of:
                current, note = prev_month(as_of[:7]), f"기준일 {as_of}의 지난달"
            else:
                note = "기준일이 없어 지난달이 언제인지 정하지 못함"
        if current and current not in months:
            raise ValueError(f"{current} 주문 데이터가 없음 (DB에 있는 달: {', '.join(months)})")
        base = prev_month(current) if current else None
        if base and base not in months:
            raise ValueError(f"{base} 주문 데이터가 없음 (DB에 있는 달: {', '.join(months)})")
        return {"question": question, "as_of": as_of, "current_month": current, "base_month": base,
                "period_note": note, "months": months, "db": str(db)}

    # ② 도구: DB 구조 읽기와 읽기 전용 조회만 ─────────────────────────────────
    sql_calls = 0

    @tool
    def load_skill(name: str) -> str:
        """이름으로 skill 문서를 읽는다. SQL을 짜기 전에 DB의 테이블 구조와 관계를 확인할 때 쓴다."""
        return read_skill(name)

    @tool
    def run_sql(query: str) -> str:
        """Z몰 주문 DB(SQLite)에 SELECT 문 하나를 읽기 전용으로 실행하고 결과를 표로 돌려준다(최대 200행). 자연로그 함수 ln(x)를 쓸 수 있다."""
        nonlocal sql_calls
        sql_calls += 1
        if sql_calls > SQL_HARD_CALLS:
            raise RuntimeError(f"run_sql을 판단 한 번에 {SQL_HARD_CALLS}번 넘게 부름. GROUP BY로 묶어 횟수를 줄인다")
        if sql_calls > SQL_MAX_CALLS:
            return (f"조회 상한({SQL_MAX_CALLS}번)에 도달함. 더 조회하지 말고 지금까지 확인한 값으로 결과를 제출한다. "
                    "상한에 닿은 것은 status를 바꿀 이유가 아니다. status는 기여율로만 정한다(틀린 값은 코드가 원본 값으로 돌려준다)")
        return run_readonly_sql(db, query)

    # ⑤ 검증기: 모델이 적은 것을 원본에서 다시 계산해 비교 ─────────────────────
    def months_of(d: SalesDecision, inp: dict) -> tuple[str, str]:
        """코드가 정한 기간이 있으면 그 기간, 없으면(되물어 받은 경우) 모델이 적은 기간."""
        return (inp["base_month"], inp["current_month"]) if inp["current_month"] else (d.base_month, d.current_month)

    def check_period(d: SalesDecision, inp: dict) -> list[str]:
        months, cur, base = inp["months"], inp["current_month"], inp["base_month"]
        if cur:
            if (d.current_month, d.base_month) != (cur, base):
                return [f"분석 기간이 다름: {inp['period_note']}은 {cur}, 비교할 달은 그 전달 {base}. "
                        f"current_month={cur}, base_month={base}로 적는다 "
                        f"(적은 값: current_month={d.current_month}, base_month={d.base_month})"]
            return []
        if d.current_month not in months:
            return [f"분석할 달 {d.current_month}의 주문 데이터가 없음 (DB에 있는 달: {', '.join(months)}). "
                    "분석할 달을 모르면 needs_info로 묻는다"]
        if d.base_month != prev_month(d.current_month):
            return [f"비교할 달은 분석할 달의 전달인 {prev_month(d.current_month)} (적은 값: {d.base_month})"]
        if d.base_month not in months:
            return [f"비교할 달 {d.base_month}의 주문 데이터가 없음 (DB에 있는 달: {', '.join(months)})"]
        return []

    def check_metrics(d: SalesDecision, a: Analysis) -> list[str]:
        errors, overall = [], set()
        rows = {r.name: r for r in a.channels}
        for m in d.metrics:
            if m.channel and m.channel not in rows:
                errors.append(f"channel {m.channel!r}은 DB에 없는 값. 전체는 빈 문자열, 채널은 {' 또는 '.join(rows)}로 적는다")
                continue
            b, c = ((rows[m.channel].base, rows[m.channel].current) if m.channel else (a.base, a.current))
            b, c = b[m.metric], c[m.metric]
            tol = AOV_TOLERANCE if m.metric == "aov" else TOLERANCE[METRICS[m.metric]["unit"]]
            if abs(m.base - b) > tol or abs(m.current - c) > tol:
                percent = METRICS[m.metric]["unit"] == "%"
                wrote = f"{m.base:g} → {m.current:g}" if percent else f"{fmt(m.metric, m.base)} → {fmt(m.metric, m.current)}"
                fraction = percent and abs(m.base * 100 - b) <= 0.5 and abs(m.current * 100 - c) <= 0.5
                errors.append(f"{m.metric}({m.channel or '전체'}) 값이 원본과 다름: {a.base_month} {fmt(m.metric, b)} → "
                              f"{a.current_month} {fmt(m.metric, c)} (적은 값: {wrote})"
                              + (f". 비율은 0~1 소수가 아니라 백분율 숫자로 적는다({m.base:g}가 아니라 {b:.1f})" if fraction else ""))
            if not m.channel:
                overall.add(m.metric)
        if any(e.startswith(("revenue(", "aov(")) and "값이 원본과 다름" in e for e in errors):
            errors.append("매출·객단가가 원본보다 크면 order_items와 합친 뒤 payments.amount를 더해 상품 줄 수만큼 중복된 것이다. "
                          "매출은 주문마다 결제 금액을 한 번만 더한다")
        missing = [k for k in ("revenue", *FACTORS) if k not in overall]
        if missing:
            errors.append("metrics에 전체 지표가 빠짐(매출과 일곱 요인은 반드시 적는다): "
                          + ", ".join(f"{k} {fmt(k, a.base[k])} → {fmt(k, a.current[k])}" for k in missing))
        return errors

    def check_text(d: SalesDecision, a: Analysis) -> list[str]:
        """문장에는 숫자를 직접 쓰지 않는다. 자리표시가 원본에 있는 값을 가리키는지만 확인하면 된다."""
        text, errors = "\n".join([d.summary, *d.notes]), []
        written = written_numbers(text)
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", text) if written_numbers(s)]
        if written:
            keys = ["revenue.base", "revenue.current", "revenue.change"]
            if a.primary:
                keys += [f"{a.primary}.base", f"{a.primary}.current", f"{a.primary}.share"]
            keys += [f"{r.name}.revenue.share" for r in a.channels if r.share_of_change is not None]
            examples = ", ".join(f"{{{k}}} = {fill_numbers('{' + k + '}', a)[0]}" for k in keys)
            quoted = " / ".join(f"「{s}」" for s in sentences[:3])
            errors.append(f"summary·notes에 숫자({', '.join(written)})를 직접 씀: {quoted}. 이 문장의 숫자를 자리표시로 바꾸거나 "
                          f"지운다(코드가 원본 값으로 채움). 예: {examples}. 기준값이나 100% 같은 숫자도 숫자 없이 "
                          "'자동 저장 기준', '몰림 기준'처럼 말로 쓴다. 날짜(5월, 2026년)는 그대로 써도 된다")
        bad = fill_numbers(text, a)[1]
        if bad:
            errors.append(f"쓸 수 없는 자리표시 {', '.join(bad)}. 형식은 {{지표.base}}, {{지표.current}}, {{지표.change}}, "
                          f"{{지표.share}}이고 채널 값은 {{web.지표.current}}처럼 적는다. 지표 키: {', '.join(METRICS)}. "
                          f"채널: {', '.join(r.name for r in a.channels)}. share는 일곱 요인과 채널 매출에만 쓴다"
                          + ("" if a.shares else ". 이번 비교는 요인으로 나눌 수 없어 share가 없다"))
        if UNIT_AFTER_PLACEHOLDER.search(text):
            errors.append("자리표시 뒤에 단위(원·건·%)를 붙이지 않는다. 채울 때 단위가 함께 들어간다")
        return errors

    def check_cause(d: SalesDecision, a: Analysis) -> list[str]:
        if a.primary is None:    # 변화가 없거나, 한쪽 달의 매출·결제가 0이라 요인으로 나눌 수 없다
            reason = (f"매출 변화가 ±{FLAT_PCT}% 미만(flat)" if a.direction == "flat"
                      else "한쪽 달의 매출이나 결제가 0이라 요인으로 나눌 수 없는 경우")
            return [f"{reason}이면 primary_cause와 primary_channel을 비운다"] if d.primary_cause or d.primary_channel else []
        errors = []
        if d.primary_cause != a.primary:
            errors.append(f"primary_cause는 {a.primary}: 요인별 기여율 {a.shares_text()} "
                          "(기여율 = 요인의 로그 변화 ÷ 매출의 로그 변화, 가장 큰 요인)")
        shares = ", ".join(f"{r.name}은 전체 변화의 {pct(r.share_of_change)}(비교 달 매출 비중 {pct(r.base_share)})"
                           for r in a.channels)
        rule = f"몰렸다고 보려면 전체 변화의 {CONCENTRATION_SHARE:.0%} 이상이고 매출 비중의 {CONCENTRATION_LIFT}배 이상이어야 함"
        if d.primary_channel and d.primary_channel not in a.concentrated:
            hint = f"몰린 채널은 {', '.join(a.concentrated)}" if a.concentrated else "몰린 채널이 없으므로 primary_channel을 비운다"
            errors.append(f"primary_channel {d.primary_channel}: {shares}. {rule}. {hint}")
        elif not d.primary_channel and a.concentrated:
            errors.append(f"매출 변화가 {a.concentrated[0]}에 몰림: {shares}. primary_channel에 {a.concentrated[0]}를 적는다")
        if d.status == "completed" and a.primary_share < AUTO_SHARE:
            errors.append(f"주원인 {a.primary}의 기여율이 {pct(a.primary_share)}로 {AUTO_SHARE:.0%} 미만이라 원인이 여러 요인에 "
                          f"나뉨. completed가 아니라 needs_review로 올리고 notes에 확인할 점을 적는다 ({a.shares_text()})")
        return errors

    def verify(d: SalesDecision, inp: dict) -> list[str]:
        if d.status == "failed":
            return []        # 처리 불가 판단은 아무것도 반영하지 않고 끝나므로 안전한 쪽이다
        if d.status == "needs_info":
            return ([f"{inp['period_note']}로 분석할 달({inp['current_month']})과 비교할 달({inp['base_month']})이 "
                     "정해져 있으므로 되묻지 말고 분석한다"] if inp["current_month"] else [])
        errors = check_period(d, inp)
        if errors and not inp["current_month"]:
            return errors    # 비교할 두 달을 모르면 숫자를 확인할 수 없다
        with closing(connect_ro(db)) as conn:    # 코드가 정한 기간이 있으면 모델이 기간을 틀려도 나머지를 한 번에 확인한다
            a = analyze(conn, *months_of(d, inp))
        if (d.revenue_base, d.revenue_current) != (a.revenue_base, a.revenue_current):
            errors.append(f"매출이 원본과 다름: {a.base_month} {fmt('revenue', a.revenue_base)} → {a.current_month} "
                          f"{fmt('revenue', a.revenue_current)} ({a.change_pct:+.1f}%). 적은 값: {d.revenue_base} → "
                          f"{d.revenue_current}. 매출은 결제 승인 금액 합(할인 차감 후)")
        if d.direction != a.direction:
            errors.append(f"direction은 {a.direction}이어야 함: 매출이 {a.change_pct:+.1f}% 변함"
                          f"(±{FLAT_PCT}% 미만이면 flat). 질문의 전제와 달라도 데이터대로 적는다")
        return errors + check_metrics(d, a) + check_text(d, a) + check_cause(d, a)

    # ⑥ 승인 정책: 원인이 분명할 때만 자동 저장, 나머지는 담당자 확인 ──────────
    def approval_policy(d: SalesDecision, inp: dict) -> str:
        if d.status != "completed":
            return "human"
        with closing(connect_ro(db)) as conn:   # 판단 결과가 아니라 원본으로 기여율을 다시 계산한다
            a = analyze(conn, *months_of(d, inp))
        return "auto" if a.primary_share is not None and a.primary_share >= AUTO_SHARE else "human"

    # ⑦ 반영 어댑터: 분석 보고서를 파일로 저장(실제 배포·발송 없음) ────────────────
    def commit(d: SalesDecision, inp: dict, request_id: str) -> str:
        with closing(connect_ro(db)) as conn:
            a = analyze(conn, *months_of(d, inp))
        report_dir.mkdir(parents=True, exist_ok=True)
        path = report_dir / f"{request_id}.md"
        path.write_text(report_markdown(d, inp, a, request_id), encoding="utf-8")
        return str(path)

    if decide is None and model is not None:   # ②③④를 묶어 실제 LLM 판단 단계로
        decide = agent_decider(model, [load_skill, run_sql], INSTRUCTIONS, SalesDecision, render)
    if decide is None:
        from .fake_decider import fake_decide
        decide = fake_decide

    previous: dict[tuple, tuple[str, int]] = {}   # (질문, 기준일) → (직전 판단, 그때까지 받은 피드백 수)

    def decide_once(ctx: DecideContext) -> SalesDecision:
        """다시 판단할 때는 직전 판단과 새 피드백만 넘긴다. 처음부터 다시 조회하면 맞았던 값이 흔들린다(16.6).
        검증기는 되물은 답을 보지 못하므로(골격 구조), 묻지 않고 달을 정한 판단은 여기서 돌려보낸다."""
        nonlocal sql_calls
        sql_calls = 0      # 판단마다 SQL 횟수를 새로 센다
        inp, key, feedback = ctx.job_input, (ctx.job_input["question"], ctx.job_input["as_of"]), ctx.feedback
        last, seen = previous.get(key, (None, 0))
        if feedback and last and feedback[seen:]:
            ctx = DecideContext({**inp, "previous_decision": last}, ctx.clarifications, feedback[seen:])
        d = decide(ctx)
        if not isinstance(d, SalesDecision):
            return d       # 결과 형식으로 답하지 않았으면 골격이 모델 오류로 센다
        previous[key] = (d.model_dump_json(exclude_none=True), len(feedback))
        if d.status in ("completed", "needs_review") and not inp["current_month"] and not ctx.clarifications:
            raise ValueError(f"분석할 달이 정해지지 않았는데 묻지 않고 {d.current_month}로 분석함. "
                             f"추측하지 말고 needs_info로 묻는다 (DB에 있는 달: {', '.join(inp['months'])})")
        answered = months_in_text(ctx.clarifications[-1]["answer"], inp["months"]) if ctx.clarifications else []
        if d.status == "needs_info" and len(answered) == 1 and answered[0] in inp["months"]:
            raise ValueError(f"분석할 달은 사용자가 답한 {answered[0]}로 정해졌으므로 더 묻지 말고 분석한다 "
                             f"(current_month={answered[0]}, base_month={prev_month(answered[0])})")
        if inp["current_month"]:   # 코드가 정한 기간은 모델이 비워도 채운다(다르게 적으면 검증기가 잡는다)
            d = d.model_copy(update={"current_month": d.current_month or inp["current_month"],
                                     "base_month": d.base_month or inp["base_month"]})
        return d

    # 근거는 metrics의 지표 값을 원본에서 다시 계산해 검증하므로 공통 evidence 검사는 끈다
    return JobSpec(name="commerce_analysis", decision_model=SalesDecision, load_input=load_input, decide=decide_once,
                   verify=verify, approval_policy=approval_policy, commit=commit, require_evidence=False)
