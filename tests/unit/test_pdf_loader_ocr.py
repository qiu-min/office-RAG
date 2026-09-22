"""Unit tests for the OCR -> MarkItDown PDF loading path."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.libs.loader import pdf_loader as pdf_loader_module
from src.libs.loader.pdf_loader import PdfLoader


class FakeMarkItDown:
    def __init__(self, calls):
        self.calls = calls

    def convert(self, path: str):
        path = Path(path)
        self.calls.append(path)
        assert path.exists()
        return SimpleNamespace(text_content="OCR text converted to Markdown")


def test_image_only_pdf_ocr_runs_before_markitdown(tmp_path, monkeypatch):
    source = tmp_path / "scanned.pdf"
    source.write_bytes(b"not parsed by the mocked loader")
    calls = []

    loader = PdfLoader(extract_images=False)
    loader._markitdown = FakeMarkItDown(calls)
    monkeypatch.setattr(loader, "_extract_text", lambda path: "")

    def fake_create_ocr_pdf(pdf_path: Path, output_path: Path) -> Path:
        output_path.write_bytes(b"searchable OCR PDF")
        return output_path

    monkeypatch.setattr(loader, "_create_ocr_pdf", fake_create_ocr_pdf)

    document = loader.load(source)

    assert document.text == "OCR text converted to Markdown"
    assert document.metadata["text_extraction"] == "ocr+markitdown"
    assert len(calls) == 1
    assert calls[0] != source


def test_text_pdf_skips_ocr_and_uses_markitdown(tmp_path, monkeypatch):
    source = tmp_path / "text.pdf"
    source.write_bytes(b"has a text layer")
    calls = []

    loader = PdfLoader(extract_images=False)
    loader._markitdown = FakeMarkItDown(calls)
    monkeypatch.setattr(loader, "_extract_text", lambda path: "Existing PDF text")

    def fail_if_ocr_is_called(pdf_path: Path, output_path: Path) -> Path:
        raise AssertionError("OCR should not run for a text PDF")

    monkeypatch.setattr(loader, "_create_ocr_pdf", fail_if_ocr_is_called)

    document = loader.load(source)

    assert document.metadata["text_extraction"] == "markitdown"
    assert calls == [source]


def test_missing_tesseract_dependency_has_clear_error(tmp_path, monkeypatch):
    source = tmp_path / "scanned.pdf"
    source.write_bytes(b"image-only PDF")

    loader = PdfLoader(extract_images=False)
    monkeypatch.setattr(loader, "_extract_text", lambda path: "")
    monkeypatch.setattr(pdf_loader_module, "TESSERACT_AVAILABLE", False)

    with pytest.raises(RuntimeError, match="pytesseract"):
        loader.load(source)
