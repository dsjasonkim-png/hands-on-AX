"""공용 OCR 어댑터 테스트. 실습 장의 샘플 없이, 테스트 안에서 만든 이미지로 확인한다."""
import shutil
import subprocess
from pathlib import Path

import pytest

from axtools.ocr import CachedOCR, OCRLine, OCRResult, TesseractOCR

FONT = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")


def make_image(path: Path, lines: list[str]) -> Path:
    """흰 바탕에 글자를 그린 작은 이미지를 만든다(글꼴이 없으면 기본 글꼴)."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(str(FONT), 30) if FONT.exists() else ImageFont.load_default()
    img = Image.new("RGB", (620, 60 + 51 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((40, 30 + i * 51), line, font=font, fill="black")
    img.save(path)
    return path


def test_cached_ocr_reads_saved_result(tmp_path):
    img = make_image(tmp_path / "doc.png", ["합계 18,400원"])
    saved = OCRResult(source=str(img), engine="t", lines=[OCRLine(id="L1", text="합계 18,400원", conf=90)])
    CachedOCR.cache_path(img).write_text(saved.model_dump_json(), encoding="utf-8")
    r = CachedOCR(None).read(img)
    assert r.lines[0].id == "L1" and any("합계" in l.text for l in r.lines)


def test_cache_missing_without_engine_raises(tmp_path):
    img = make_image(tmp_path / "x.png", ["캐시 없음"])
    with pytest.raises(FileNotFoundError):
        CachedOCR(None).read(img)


def test_low_confidence_lines():
    r = OCRResult(source="x", engine="t", lines=[OCRLine(id="L1", text="a", conf=90), OCRLine(id="L2", text="b", conf=40)])
    assert r.low_confidence(65) == ["L2"]


def has_korean_tesseract() -> bool:
    if shutil.which("tesseract") is None:
        return False
    out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True)
    return "kor" in (out.stdout + out.stderr).split()


@pytest.mark.skipif(not has_korean_tesseract() or not FONT.exists(), reason="Tesseract 한국어 데이터 또는 글꼴 없음")
def test_tesseract_reads_korean(tmp_path):
    img = make_image(tmp_path / "doc.png", ["Z몰 비품 신청", "합계          18,400원"])
    r = TesseractOCR().read(img)
    joined = "".join(l.text for l in r.lines).replace(" ", "")
    assert "합계" in joined and "18,400" in joined and r.lines[0].id == "L1"
