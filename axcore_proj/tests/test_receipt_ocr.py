"""17장 영수증 샘플을 OCR 어댑터로 읽는 테스트. 저장된 OCR 결과(캐시)와 실제 Tesseract 결과를 확인한다."""
import shutil
import subprocess
from pathlib import Path

import pytest

from axtools.ocr import CachedOCR, TesseractOCR

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "receipt"


def test_cached_ocr_reads_receipt_sample():
    r = CachedOCR(None).read(SAMPLES / "R001_taxi.png")
    assert r.lines[0].id == "L1" and any("합계" in l.text for l in r.lines)


def has_korean_tesseract() -> bool:
    if shutil.which("tesseract") is None:
        return False
    out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True)
    return "kor" in (out.stdout + out.stderr).split()


@pytest.mark.skipif(not has_korean_tesseract(), reason="Tesseract 한국어 데이터 없음")
def test_tesseract_reads_receipt_sample():
    r = TesseractOCR().read(SAMPLES / "R001_taxi.png")
    joined = "".join(l.text for l in r.lines).replace(" ", "")
    assert "합계" in joined and "18,400" in joined
