"""가짜 판단기: API 키 없이 흐름을 확인하기 위한 규칙 기반 대역 (판단 품질의 기준이 아님).

실제 모델은 run_sql로 숫자를 찾아 가지만, 대역은 정답 계산(metrics.py)을 그대로 불러 결과 형식을 채운다.
문장에는 실제 모델처럼 숫자 대신 자리표시를 쓴다.
"""
from __future__ import annotations

from contextlib import closing

from axcore import DecideContext

from .metrics import FACTORS, METRICS, analyze, connect_ro, months_in_text, prev_month
from .policies import AUTO_SHARE
from .spec import MetricValue, SalesDecision


def fake_decide(ctx: DecideContext) -> SalesDecision:
    inp = ctx.job_input
    cur, base = inp["current_month"], inp["base_month"]
    if cur is None:   # 코드가 기간을 정하지 못했으면 되물어 받은 답에서 분석할 달을 찾는다
        for c in ctx.clarifications:
            found = months_in_text(c["answer"], inp["months"])
            if found:
                cur, base = found[0], prev_month(found[0])
    if cur is None:
        return SalesDecision(status="needs_info", summary="분석할 달이 주어지지 않음",
                             question="어느 달 매출을 분석할까요? (예: 2026년 9월)")
    with closing(connect_ro(inp["db"])) as conn:
        a = analyze(conn, base, cur)
    metrics = [MetricValue(metric=k, base=a.base[k], current=a.current[k]) for k in ("revenue", *FACTORS)]
    for name in a.concentrated:
        row = next(r for r in a.channels if r.name == name)
        metrics.append(MetricValue(metric="revenue", channel=name, base=row.base["revenue"], current=row.current["revenue"]))
    moved = {"up": "늘었다", "down": "줄었다", "flat": "거의 그대로다"}[a.direction]
    summary = f"{cur} 매출은 {base}보다 {{revenue.change}} {moved}."
    if a.primary:
        summary += f" 가장 크게 움직인 요인은 {METRICS[a.primary]['name']}(기여율 {{{a.primary}.share}})다."
    if a.concentrated:
        summary += f" 변화의 {{{a.concentrated[0]}.revenue.share}}가 {a.concentrated[0]} 채널에서 나왔다."
    clear = a.primary_share is None or a.primary_share >= AUTO_SHARE
    notes = [] if clear else ["원인이 여러 요인에 나뉨: " + ", ".join(f"{METRICS[k]['name']} {{{k}.share}}" for k in FACTORS)
                              + ". 요인마다 담당 부서와 원인을 확인한다"]
    return SalesDecision(status="completed" if clear else "needs_review", summary=summary,
                         current_month=cur, base_month=base, revenue_base=a.revenue_base,
                         revenue_current=a.revenue_current, direction=a.direction, primary_cause=a.primary,
                         primary_channel=a.concentrated[0] if a.concentrated else None, metrics=metrics, notes=notes)
