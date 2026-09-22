"""Unit tests for the Business Golden Set validator."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.validate_golden_set import main, validate_dataset


def _case(**overrides: object) -> dict:
    case = {
        "id": "biz_001",
        "query": "How do I enable the firewall?",
        "reference_answer": "Run sudo ufw enable.",
        "evidence": [{"source": "doc.pdf", "text": "sudo ufw enable"}],
        "expected_sources": ["doc.pdf"],
        "expected_chunk_ids": ["chunk-a"],
        "category": "procedure",
        "difficulty": "easy",
        "review_status": "pending",
    }
    case.update(overrides)
    return case


def _dataset(*cases: dict) -> dict:
    return {
        "name": "business_golden_test",
        "version": "1.0",
        "source_root": "sources",
        "test_cases": list(cases),
    }


def _chunk_metadata() -> dict:
    return {"chunk-a": {"source": "doc.pdf", "text": "sudo ufw enable"}}


def _validate(tmp_path: Path, data: dict):
    source_root = tmp_path / "sources"
    source_root.mkdir(exist_ok=True)
    (source_root / "doc.pdf").write_bytes(b"fixture")
    return validate_dataset(
        data,
        dataset_path=tmp_path / "dataset.json",
        chunk_metadata=_chunk_metadata(),
    )


def test_valid_golden_set(tmp_path: Path) -> None:
    result = _validate(tmp_path, _dataset(_case()))
    assert result.is_valid
    assert result.valid_cases == 1


def test_duplicate_id_is_invalid() -> None:
    result = validate_dataset(_dataset(_case(), _case()), chunk_metadata=_chunk_metadata())
    assert not result.is_valid
    assert any("duplicate id" in error for error in result.errors)


def test_empty_query_is_invalid() -> None:
    result = validate_dataset(_dataset(_case(query="")), chunk_metadata=_chunk_metadata())
    assert not result.is_valid


def test_empty_reference_answer_is_invalid() -> None:
    result = validate_dataset(
        _dataset(_case(reference_answer="")), chunk_metadata=_chunk_metadata()
    )
    assert not result.is_valid


def test_empty_evidence_is_invalid() -> None:
    result = validate_dataset(_dataset(_case(evidence=[])), chunk_metadata=_chunk_metadata())
    assert not result.is_valid


def test_invalid_category_is_invalid() -> None:
    result = validate_dataset(_dataset(_case(category="other")), chunk_metadata=_chunk_metadata())
    assert not result.is_valid


def test_invalid_difficulty_is_invalid() -> None:
    result = validate_dataset(_dataset(_case(difficulty="very_hard")), chunk_metadata=_chunk_metadata())
    assert not result.is_valid


def test_invalid_review_status_is_invalid() -> None:
    result = validate_dataset(_dataset(_case(review_status="approved")), chunk_metadata=_chunk_metadata())
    assert not result.is_valid


def test_missing_source_file_is_invalid(tmp_path: Path) -> None:
    result = _validate(tmp_path, _dataset(_case(evidence=[{"source": "missing.pdf", "text": "x"}], expected_sources=["missing.pdf"])))
    assert not result.is_valid
    assert any("does not exist" in error for error in result.errors)


def test_evidence_source_must_be_expected_source() -> None:
    result = validate_dataset(
        _dataset(_case(evidence=[{"source": "other.pdf", "text": "x"}])),
        chunk_metadata=_chunk_metadata(),
    )
    assert not result.is_valid
    assert any("not in expected_sources" in error for error in result.errors)


def test_duplicate_chunk_ids_are_invalid() -> None:
    result = validate_dataset(
        _dataset(_case(expected_chunk_ids=["chunk-a", "chunk-a"])),
        chunk_metadata=_chunk_metadata(),
    )
    assert not result.is_valid


def test_unresolved_case_may_have_no_chunk_ids(tmp_path: Path) -> None:
    result = _validate(
        tmp_path,
        _dataset(_case(review_status="unresolved", expected_chunk_ids=[])),
    )
    assert result.is_valid


def test_pending_case_requires_chunk_ids() -> None:
    result = validate_dataset(
        _dataset(_case(review_status="pending", expected_chunk_ids=[])),
        chunk_metadata=_chunk_metadata(),
    )
    assert not result.is_valid


def test_reviewed_case_requires_chunk_ids() -> None:
    result = validate_dataset(
        _dataset(_case(review_status="reviewed", expected_chunk_ids=[])),
        chunk_metadata=_chunk_metadata(),
    )
    assert not result.is_valid


def test_valid_cli_returns_zero(tmp_path: Path, capsys) -> None:
    path = tmp_path / "golden.json"
    path.write_text(json.dumps(_dataset(_case())), encoding="utf-8")
    source_root = tmp_path / "sources"
    source_root.mkdir()
    (source_root / "doc.pdf").write_bytes(b"fixture")
    assert main(["--test-set", str(path), "--skip-chunk-storage"]) == 0
    assert "Valid: 1" in capsys.readouterr().out


def test_invalid_cli_returns_nonzero(tmp_path: Path, capsys) -> None:
    invalid = _dataset(_case(query=""))
    path = tmp_path / "golden.json"
    path.write_text(json.dumps(invalid), encoding="utf-8")
    assert main(["--test-set", str(path), "--skip-chunk-storage"]) != 0
    assert "Errors:" in capsys.readouterr().out


def test_chunk_source_must_match_expected_sources() -> None:
    metadata = {"chunk-a": {"source": "another.pdf", "text": "x"}}
    result = validate_dataset(_dataset(_case()), chunk_metadata=metadata)
    assert not result.is_valid
    assert any("maps to" in error for error in result.errors)


def test_unknown_chunk_id_is_invalid() -> None:
    result = validate_dataset(
        _dataset(_case(expected_chunk_ids=["missing-chunk"])),
        chunk_metadata=_chunk_metadata(),
    )
    assert not result.is_valid
