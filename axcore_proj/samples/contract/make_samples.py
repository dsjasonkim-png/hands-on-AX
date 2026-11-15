"""18장 계약서 검토의 합성 계약서(텍스트·스캔본·일부 페이지가 빠진 스캔본)를 만든다.
python -m samples.contract.make_samples

실제 회사·사람 정보가 아닌 가상 데이터다. 스캔 느낌을 내려고 약간 기울이고 흐리게 만든다.
OCR 결과는 이미지 옆의 <이름>.ocr.json에 저장돼 있다(axtools.ocr.CachedOCR).
"""
from __future__ import annotations

import random
import re
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).parent
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

CONTRACT = """물류 위탁 계약서

주식회사 Z몰(이하 "갑")과 한빛로지스 주식회사(이하 "을")는 다음과 같이 계약을 체결한다.

제1조 (목적) 본 계약은 갑의 상품 보관 및 배송 업무를 을에게 위탁하는 데 필요한 사항을 정한다.

제2조 (계약 기간) 본 계약의 기간은 2026년 10월 1일부터 2027년 9월 30일까지로 한다. 기간 만료 30일 전까지 서면 통지가 없으면 동일한 조건으로 1년간 자동 연장된다.

제3조 (대금 지급) 갑은 매월 말일 기준 검수가 완료된 물량에 대하여 익월 60일 이내에 대금을 지급한다.

제4조 (손해배상) 을의 귀책사유로 갑에게 손해가 발생한 경우 을은 이를 배상한다. 다만 배상 총액은 직전 3개월 대금 합계를 한도로 한다.

제5조 (계약 해지) 각 당사자는 30일 전 서면 통지로 본 계약을 해지할 수 있다.

제6조 (분쟁 해결) 본 계약과 관련한 분쟁은 서울중앙지방법원을 관할 법원으로 한다.
"""


def scan_like(img: Image.Image, seed: int) -> Image.Image:
    rnd = random.Random(seed)
    img = img.rotate(rnd.uniform(-0.8, 0.8), expand=True, fillcolor="white")
    return img.filter(ImageFilter.GaussianBlur(0.4))


def render_lines(lines: list[str], width: int, size: int) -> Image.Image:
    font = ImageFont.truetype(FONT, size)
    line_h = int(size * 1.7)
    img = Image.new("RGB", (width, 60 + line_h * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((40, 30 + i * line_h), line, font=font, fill="black")
    return img


def wrap(text: str, width_chars: int) -> list[str]:
    out = []
    for para in text.splitlines():
        out.extend(textwrap.wrap(para, width_chars) or [""])   # 단어 단위로 줄바꿈
    return out


def partial_contract() -> str:
    """CT-005용. 3쪽짜리 스캔본에서 2쪽(제4조·제5조)이 빠진 경우. 남은 쪽의 쪽 번호를 붙인다(1쪽 끝, 3쪽 끝)."""
    paras = [p for p in CONTRACT.split("\n\n") if not re.match(r"제[45]조", p)]
    out = []
    for p in paras:
        out.append(p)
        if p.startswith("제3조"):
            out.append("- 1 / 3 -")
    out.append("- 3 / 3 -")
    return "\n\n".join(out)


def main() -> None:
    (ROOT / "logistics_contract.txt").write_text(CONTRACT, encoding="utf-8")
    scan_like(render_lines(wrap(CONTRACT, 40), 1300, 28), seed=99).save(ROOT / "logistics_contract_scan.png")
    scan_like(render_lines(wrap(partial_contract(), 40), 1300, 28), seed=100).save(ROOT / "logistics_contract_scan_partial.png")
    print("계약서 샘플 생성 완료")


if __name__ == "__main__":
    main()
