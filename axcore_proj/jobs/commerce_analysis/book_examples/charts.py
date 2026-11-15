"""원고 그림(SVG). 실습 흐름(보고서 저장)에서는 쓰지 않는다. 숫자는 계산하지 않고, make_figures.py가 원본에서 다시 계산해 넘긴 값을 그리기만 한다.

새 패키지 없이 SVG 문자열을 만든다. 글자는 보는 쪽 글꼴로 그려지므로 한글 글꼴을 따로 설정할 필요가 없다.
색만으로 구분하지 않도록 막대마다 값과 ▲▼를 함께 적는다.
"""
from __future__ import annotations

from xml.sax.saxutils import escape

FONT = "'Malgun Gothic', 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif"
TEXT, SUBTEXT, GRID = "#22303c", "#5d6d7e", "#e5e8eb"
DOWN, UP, TOTAL = "#c0392b", "#2471a3", "#7f8c8d"          # 감소, 증가, 합계
BASE_BAR, CURRENT_BAR, MUTED = "#b3bfcc", "#2471a3", "#d5dbe1"
HIGHLIGHT = "#e67e22"
WIDTH = 720


def man(v: float, sign: bool = False) -> str:
    """원 → '3,713만 원'. 100만 원 단위 미만은 소수 첫째 자리까지 적는다."""
    x = v / 10_000
    body = f"{abs(x):,.0f}" if abs(x) >= 100 else f"{abs(x):,.1f}".removesuffix(".0")
    return f"{'-' if x < 0 else '+' if sign and x > 0 else ''}{body}만 원"


def _text(x: float, y: float, s: str, *, anchor: str = "start", size: int = 12, weight: str = "normal",
          fill: str = TEXT) -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}">{escape(s)}</text>')


def _rect(x: float, y: float, w: float, h: float, fill: str, stroke: str = "none") -> str:
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0):.1f}" height="{h:.1f}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="2"/>')


def _line(x1: float, y1: float, x2: float, y2: float, stroke: str = GRID, dash: str = "") -> str:
    extra = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" stroke-width="1"{extra}/>'


def _svg(title: str, subtitle: str, height: float, body: list[str]) -> str:
    label = escape(title, {'"': "&quot;"})
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height:.0f}" viewBox="0 0 {WIDTH} {height:.0f}" '
        f'font-family="{FONT}" role="img" aria-label="{label}">',
        f"<title>{escape(title)}</title>",
        f'<rect width="{WIDTH}" height="{height:.0f}" fill="#ffffff"/>',
        _text(16, 26, title, size=15, weight="bold"),
        _text(16, 44, subtitle, size=11, fill=SUBTEXT),
        *body,
        "</svg>",
    ])


def trend_svg(title: str, points: list[tuple[str, float]], roles: dict[str, str]) -> str:
    """월별 매출 세로 막대. roles = {달: '비교 달' 또는 '분석 달'}인 달을 색으로 구분한다."""
    left, right, top, bottom = 40, WIDTH - 20, 80, 270
    slot = (right - left) / max(len(points), 1)
    top_value = max((v for _, v in points), default=0) or 1
    body = [_line(left, bottom, right, bottom, SUBTEXT)]
    for i, (month, v) in enumerate(points):
        role = roles.get(month)
        color = CURRENT_BAR if role == "분석 달" else TOTAL if role == "비교 달" else MUTED
        h = (bottom - top) * v / top_value
        x = left + slot * i + slot * 0.2
        body += [_rect(x, bottom - h, slot * 0.6, h, color),
                 _text(x + slot * 0.3, bottom - h - 6, man(v).removesuffix(" 원"), anchor="middle", size=11),
                 _text(x + slot * 0.3, bottom + 16, month, anchor="middle", size=11, fill=SUBTEXT)]
        if role:
            body.append(_text(x + slot * 0.3, bottom + 32, role, anchor="middle", size=11, weight="bold", fill=color))
    return _svg(title, "막대 위 숫자는 그 달 매출(결제 승인 금액 합)", bottom + 48, body)


def funnel_svg(title: str, steps: list[tuple[str, float, float, str]], months: tuple[str, str],
               highlight: str | None = None, mark: str = "▼") -> str:
    """고객 여정 단계별 건수를 두 달 나란히 그린다. steps = [(단계, 비교 달 건수, 분석 달 건수, 오른쪽에 적을 글)].
    highlight = 주원인 단계, mark = 그 단계 앞에 붙일 표시(그 요인이 오르면 ▲, 떨어지면 ▼. make_figures.py가 정해 넘긴다)."""
    label_x, bar_x, bar_w, row_h, top = 112, 124, 400, 46, 84
    top_value = max((max(b, c) for _, b, c, _ in steps), default=0) or 1
    body = [_rect(bar_x, 54, 12, 12, BASE_BAR), _text(bar_x + 18, 64, months[0], size=11),
            _rect(bar_x + 110, 54, 12, 12, CURRENT_BAR), _text(bar_x + 128, 64, months[1], size=11),
            _text(WIDTH - 12, 64, "앞 단계에서 넘어온 비율", anchor="end", size=11, fill=SUBTEXT)]
    for i, (label, b, c, note) in enumerate(steps):
        y = top + row_h * i
        hit = label == highlight
        body += [_text(label_x, y + 19, (f"{mark} " if hit else "") + label, anchor="end", weight="bold" if hit else "normal",
                       fill=HIGHLIGHT if hit else TEXT),
                 _rect(bar_x, y, bar_w * b / top_value, 14, BASE_BAR),
                 _text(bar_x + bar_w * b / top_value + 6, y + 11, f"{b:,.0f}건", size=11, fill=SUBTEXT),
                 _rect(bar_x, y + 17, bar_w * c / top_value, 14, CURRENT_BAR, HIGHLIGHT if hit else "none"),
                 _text(bar_x + bar_w * c / top_value + 6, y + 28, f"{c:,.0f}건", size=11),
                 _text(WIDTH - 12, y + 19, note, anchor="end", size=12, weight="bold" if hit else "normal",
                       fill=HIGHLIGHT if hit else TEXT)]
    return _svg(title, "막대는 단계별 건수(세션·주문 수). 주원인 단계에 ▲(오름) 또는 ▼(떨어짐)를 붙임",
                top + row_h * len(steps) + 10, body)


def bridge_svg(title: str, start: tuple[str, float], steps: list[tuple[str, float, str]], end: tuple[str, float]) -> str:
    """매출 다리(가로 워터폴). steps = [(요인, 매출 효과(원), 오른쪽에 적을 글)]. 효과의 합이 end - start가 되게 넘긴다."""
    label_x, bar_x, bar_w, row_h, top = 128, 140, 380, 30, 66
    running, peak = start[1], max(start[1], end[1])
    for _, delta, _ in steps:
        running += delta
        peak = max(peak, running)
    scale = bar_w / (peak or 1)
    rows = [(start[0], 0.0, start[1], TOTAL, man(start[1]))]
    running = start[1]
    for label, delta, note in steps:
        lo, hi = sorted((running, running + delta))
        mark = "▼" if delta < 0 else "▲" if delta > 0 else "·"
        rows.append((label, lo, hi, DOWN if delta < 0 else UP, f"{mark} {man(delta, sign=True)} · {note}"))
        running += delta
    rows.append((end[0], 0.0, end[1], TOTAL, man(end[1])))
    body = [_line(bar_x, top - 6, bar_x, top + row_h * len(rows), SUBTEXT)]
    for i, (label, lo, hi, color, value) in enumerate(rows):
        y = top + row_h * i
        total = color == TOTAL
        body += [_text(label_x, y + 15, label, anchor="end", weight="bold" if total else "normal"),
                 _rect(bar_x + lo * scale, y + 3, max((hi - lo) * scale, 1.5 if hi != lo else 0), 18, color),
                 _text(bar_x + bar_w + 14, y + 16, value, size=11, weight="bold" if total else "normal")]
        if i < len(rows) - 1:     # 다음 막대가 시작하는 자리(누적 매출)를 점선으로 잇는다
            edge = hi if color in (TOTAL, UP) else lo
            body.append(_line(bar_x + edge * scale, y + 21, bar_x + edge * scale, y + row_h + 3, SUBTEXT, "2,2"))
    return _svg(title, "요인 효과 = 기여율 × 매출 변화. 일곱 요인의 효과를 더하면 매출 변화와 같다", top + row_h * len(rows) + 10, body)


def change_bars_svg(title: str, rows: list[tuple[str, float, str, bool]]) -> str:
    """채널별 매출 증감(0 기준 가로 막대). rows = [(채널, 증감(원), 오른쪽에 적을 글, 변화가 몰린 곳인가)]."""
    label_x, bar_x, bar_w, row_h, top = 112, 124, 330, 30, 66
    lo = min([0.0, *(d for _, d, _, _ in rows)])
    hi = max([0.0, *(d for _, d, _, _ in rows)])
    scale = bar_w / ((hi - lo) or 1)
    zero = bar_x + (0 - lo) * scale
    body = [_line(zero, top - 6, zero, top + row_h * len(rows), SUBTEXT)]
    for i, (label, delta, note, hit) in enumerate(rows):
        y = top + row_h * i
        x0, x1 = sorted((zero, zero + delta * scale))
        mark = "▼" if delta < 0 else "▲" if delta > 0 else "·"
        value = f"{mark} {man(delta, sign=True)}" + (f" · {note}" if note else "")
        body += [_text(label_x, y + 15, ("◆ " if hit else "") + label, anchor="end", weight="bold" if hit else "normal",
                       fill=HIGHLIGHT if hit else TEXT),
                 _rect(x0, y + 3, max(x1 - x0, 1.5 if delta else 0), 18, DOWN if delta < 0 else UP,
                       HIGHLIGHT if hit else "none"),
                 _text(bar_x + bar_w + 14, y + 16, value, size=11, weight="bold" if hit else "normal",
                       fill=HIGHLIGHT if hit else TEXT)]
    return _svg(title, "막대는 매출 증감(분석 달 - 비교 달). 주황 테두리와 ◆는 변화가 몰린 채널", top + row_h * len(rows) + 10, body)
