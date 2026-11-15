"""17장 영수증 정산의 합성 영수증 이미지를 만든다. python -m samples.receipt.make_samples

실제 회사·사람 정보가 아닌 가상 데이터다. 스캔 느낌을 내려고 약간 기울이고 흐리게 만든다.
OCR 결과는 이미지 옆의 <이름>.ocr.json에 저장돼 있다(axtools.ocr.CachedOCR). 이미지를 다시 만들면
같은 결과가 나오는지 확인하고, 다르면 .ocr.json을 지운 뒤 Tesseract로 다시 읽는다.
"""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).parent
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

RECEIPTS = {
    "R001_taxi.png": ["Z택시", "사업자번호 123-45-67890", "일시 2026-09-14 08:42", "-" * 24,
                      "기본요금          4,800", "주행요금         13,600", "-" * 24, "합계          18,400원", "카드 승인 완료"],
    "R002_meeting_meal.png": ["한빛한정식", "사업자번호 214-88-10293", "일시 2026-09-15 12:30", "-" * 24,
                              "한정식 4인      112,000", "음료              14,000", "-" * 24, "합계         126,000원", "카드 승인 완료"],
    "R003_stationery.png": ["Z문구", "사업자번호 305-12-44821", "일시 2026-09-16 17:05", "-" * 24,
                            "볼펜 10자루       12,000", "노트 5권           8,000", "-" * 24, "합계          25,000원", "현금"],
    # R001과 같은 영수증을 다시 찍어 제출한 경우(중복 제출)
    "R004_taxi_again.png": ["Z택시", "사업자번호 123-45-67890", "일시 2026-09-14 08:42", "-" * 24,
                            "기본요금          4,800", "주행요금         13,600", "-" * 24, "합계          18,400원", "카드 승인 완료"],
    # 금액 부분이 흐리게 찍힌 영수증(OCR 신뢰도가 낮아 사람이 확인해야 하는 경우)
    "R005_taxi_blurry.png": ["Z택시", "사업자번호 123-45-67890", "일시 2026-09-17 21:15", "-" * 24,
                             "기본요금          4,800", "주행요금          9,200", "-" * 24, "합계          14,000원", "카드 승인 완료"],
    # 업무와 무관한 업종(EXP-06)
    "R006_pub.png": ["한빛주점", "사업자번호 507-31-22904", "일시 2026-09-18 22:40", "-" * 24,
                     "생맥주 6잔        30,000", "안주 2개          36,000", "-" * 24, "합계          66,000원", "카드 승인 완료"],
}
BLURRY_LINES = {"R005_taxi_blurry.png": range(4, 8)}   # 금액 줄(기본요금 ~ 합계)만 더 흐리게
BLUR = 2.8   # 숫자는 맞게 읽히고 신뢰도는 기준(65)보다 낮게 나오는 정도. 더 흐리면 숫자를 잘못 읽는다


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


def blur_lines(img: Image.Image, rows, size: int = 30) -> Image.Image:
    """render_lines와 같은 줄 간격으로, 지정한 줄만 번지게 한다."""
    line_h = int(size * 1.7)
    for i in rows:
        box = (0, 30 + i * line_h - 6, img.width, 30 + (i + 1) * line_h - 6)
        img.paste(img.crop(box).filter(ImageFilter.GaussianBlur(BLUR)), box[:2])
    return img


def main() -> None:
    for seed, (name, lines) in enumerate(RECEIPTS.items()):   # 기울기는 순서대로 seed 0~5
        img = blur_lines(render_lines(lines, 620, 30), BLURRY_LINES.get(name, ()))
        scan_like(img, seed=seed).save(ROOT / name)
    print("영수증 샘플 생성 완료")


if __name__ == "__main__":
    main()
