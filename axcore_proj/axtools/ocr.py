"""OCR은 정해진 규칙으로 글자를 읽는 '코드'의 일이고, 읽은 글자를 해석하는 것은 AI의 일이다.
엔진은 교체할 수 있다: 로컬 Tesseract(기본) → 클라우드 OCR로 바꾸려면 read()만 구현하면 된다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel


class OCRLine(BaseModel):
    id: str          # L1, L2 ... 판단 결과가 근거 위치로 인용하는 ID
    text: str
    conf: float      # 0~100, 엔진이 준 줄 단위 평균 신뢰도


class OCRResult(BaseModel):
    source: str
    engine: str
    lines: list[OCRLine]

    def text(self, sep: str = "\n") -> str:
        return sep.join(line.text for line in self.lines)

    def low_confidence(self, threshold: float = 60) -> list[str]:
        return [line.id for line in self.lines if line.conf < threshold]


class OCREngine(Protocol):
    name: str

    def read(self, path: str | Path) -> OCRResult: ...


class TesseractOCR:
    """로컬 Tesseract. 설치: apt install tesseract-ocr tesseract-ocr-kor / pip install pytesseract"""

    name = "tesseract"

    def __init__(self, lang: str = "kor", psm: int = 6):
        self.lang, self.psm = lang, psm

    def read(self, path: str | Path) -> OCRResult:
        import pytesseract
        from PIL import Image

        d = pytesseract.image_to_data(Image.open(path), lang=self.lang, config=f"--psm {self.psm}",
                                      output_type=pytesseract.Output.DICT)
        rows: dict[tuple, list[int]] = {}
        for i, word in enumerate(d["text"]):
            if word.strip():
                rows.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(i)
        lines = []
        for n, (_, idx) in enumerate(sorted(rows.items()), start=1):
            text = d["text"][idx[0]]
            for prev, cur in zip(idx, idx[1:]):
                gap = d["left"][cur] - (d["left"][prev] + d["width"][prev])
                # 한글은 음절 단위로 쪼개져 나오므로, 글자 사이 간격이 좁으면 붙이고 넓으면 띄운다
                text += ("" if gap < 0.35 * d["height"][cur] else " ") + d["text"][cur]
            conf = sum(float(d["conf"][i]) for i in idx) / len(idx)
            lines.append(OCRLine(id=f"L{n}", text=text, conf=round(conf, 1)))
        return OCRResult(source=str(path), engine=self.name, lines=lines)


class CachedOCR:
    """이미지 옆의 <이름>.ocr.json을 먼저 읽는다. 없으면 엔진으로 읽고 저장한다.

    책의 실행 결과를 고정하고, Tesseract가 없는 독자도 같은 결과로 실습할 수 있게 한다.
    """

    def __init__(self, engine: OCREngine | None = None):
        self.engine = engine
        self.name = f"cached({engine.name if engine else 'none'})"

    @staticmethod
    def cache_path(path: str | Path) -> Path:
        return Path(path).with_suffix(".ocr.json")

    def read(self, path: str | Path) -> OCRResult:
        cache = self.cache_path(path)
        if cache.exists():
            return OCRResult.model_validate(json.loads(cache.read_text(encoding="utf-8")))
        if self.engine is None:
            raise FileNotFoundError(f"OCR 캐시가 없고 엔진도 없음: {cache}")
        result = self.engine.read(path)
        cache.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return result


def default_ocr() -> OCREngine:
    """Tesseract가 설치돼 있으면 캐시+Tesseract, 아니면 캐시만 쓴다."""
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return CachedOCR(TesseractOCR())
    except Exception:
        return CachedOCR(None)
