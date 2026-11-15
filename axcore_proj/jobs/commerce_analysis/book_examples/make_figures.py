"""원고 그림 만들기: 세 예제의 그래프(SVG)를 Q1/·Q2/·Q3/ 폴더에 그린다.

    python -m jobs.commerce_analysis.book_examples.make_figures

실습 흐름(보고서 저장)과 떼어 낸 원고용 스크립트다. 값은 모델이 아니라 코드가 원본 DB에서 다시 계산한 것(metrics.analyze)이고,
보고서 표와 같은 값이다. 모델을 부르지 않으므로 몇 번 돌려도 같은 그림이 나온다.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path

from ..metrics import FACTORS, METRICS, Analysis, analyze, connect_ro, fmt, months_in, pct, value
from ..spec import DEFAULT_DB, JOURNEY, change
from .charts import bridge_svg, change_bars_svg, funnel_svg, trend_svg

HERE = Path(__file__).parent
EXAMPLES = {"Q1": ("2026-04", "2026-05"), "Q2": ("2026-09", "2026-10"), "Q3": ("2026-08", "2026-09")}   # 예제: (비교 달, 분석 달)
CAUSE_STEP = {"clicks": "클릭", "click_to_view": "상품 조회", "view_to_cart": "장바구니", "cart_to_checkout": "주문서 진입",
              "checkout_to_order": "주문 시도", "payment_success_rate": "결제 완료"}   # 주원인 요인 → 여정 그래프에서 강조할 단계
DRAW = {"trend": trend_svg, "funnel": funnel_svg, "bridge": bridge_svg, "channel": change_bars_svg}


def chart_inputs(a: Analysis, conn) -> dict[str, dict]:
    """그래프 이름 → 그리기 함수에 넘길 값."""
    b, c = a.base, a.current
    step = CAUSE_STEP.get(a.primary)      # 주원인이 여정 밖(객단가)이거나 없으면(변화 없음) 강조하지 않는다
    rose = step is not None and c[a.primary] > b[a.primary]   # 결제 장애 복구처럼 주원인 요인이 오른 달은 ▲
    out = {
        "trend": {"title": "월별 매출", "points": [(m, value(conn, "revenue", m)) for m in months_in(conn)],
                  "roles": {a.base_month: "비교 달", a.current_month: "분석 달"}},
        "funnel": {"title": f"고객 여정 ({a.base_month} → {a.current_month})", "months": (a.base_month, a.current_month),
                   "steps": [(label, b[k], c[k], f"{fmt(r, b[r])} → {fmt(r, c[r])}" if r else f"건수 {change(b[k], c[k])}")
                             for label, k, r in JOURNEY],
                   "highlight": step, "mark": "▲" if rose else "▼"},
    }
    if a.shares:      # 변화가 없거나 한쪽 달 매출·결제가 0이면 요인으로 나눌 수 없어 그리지 않는다
        delta = a.revenue_current - a.revenue_base
        out["bridge"] = {"title": f"매출 다리 ({a.base_month} → {a.current_month})",
                         "start": (f"{a.base_month} 매출", a.revenue_base),
                         "steps": [(METRICS[k]["name"], a.shares[k] * delta, f"기여율 {pct(a.shares[k])}") for k in FACTORS],
                         "end": (f"{a.current_month} 매출", a.revenue_current)}
    out["channel"] = {"title": f"채널별 매출 변화 ({a.base_month} → {a.current_month})",
                      "rows": [(r.name, r.current["revenue"] - r.base["revenue"],
                                f"전체 변화의 {pct(r.share_of_change)}" if r.share_of_change is not None else "",
                                r.name in a.concentrated) for r in a.channels]}
    return out


def main(db: str | Path = DEFAULT_DB, out_dir: str | Path = HERE) -> list[Path]:
    written = []
    with closing(connect_ro(db)) as conn:
        for name, (base, cur) in EXAMPLES.items():
            folder = Path(out_dir) / name
            folder.mkdir(parents=True, exist_ok=True)
            for chart, kwargs in chart_inputs(analyze(conn, base, cur), conn).items():
                path = folder / f"{chart}.svg"
                path.write_text(DRAW[chart](**kwargs), encoding="utf-8")
                written.append(path)
    return written


if __name__ == "__main__":
    for path in main():
        print(path)
