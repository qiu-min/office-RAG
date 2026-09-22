#!/usr/bin/env python3
"""Validate a Golden Set and, when available, its persisted chunk mappings.

The validator deliberately stays independent from the retrieval stack.  It
checks the dataset contract and reads Chroma's SQLite metadata tables directly
for chunk existence/source checks, so validation does not initialise an
embedding model, vector store client, or external service.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping


VALID_CATEGORIES = {
    "feature",
    "configuration",
    "procedure",
    "troubleshooting",
    "limitation",
    "concept",
    "comparison",
    "cross_section",
}
VALID_DIFFICULTIES = {"easy", "medium", "hard"}
VALID_REVIEW_STATUSES = {"pending", "reviewed", "unresolved"}
DEFAULT_COLLECTION = "ubuntu_ops_filtered"


@dataclass
class ValidationResult:
    """Structured validation output used by the CLI and unit tests."""

    dataset_name: str = ""
    total_cases: int = 0
    valid_cases: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    review_status: Counter[str] = field(default_factory=Counter)
    difficulty: Counter[str] = field(default_factory=Counter)
    categories: Counter[str] = field(default_factory=Counter)
    sources: Counter[str] = field(default_factory=Counter)
    resolved_chunk_cases: int = 0
    unresolved_chunk_cases: int = 0
    chunk_storage_available: bool = False

    @property
    def is_valid(self) -> bool:
        return not self.errors


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _split_evidence_sentences(text: str) -> list[str]:
    """Split evidence at sentence-ending punctuation without normalizing it."""

    parts = re.split(r"(?<=[.!?。！？])", text)
    return [part.strip() for part in parts if part.strip()]


def _evidence_supported_by_chunks(
    evidence_text: str,
    candidate_texts: list[str],
) -> bool:
    """Check exact evidence support, with a sentence-level cross-chunk fallback."""

    if any(evidence_text in chunk_text for chunk_text in candidate_texts):
        return True

    sentences = _split_evidence_sentences(evidence_text)
    if len(sentences) <= 1:
        return False

    return all(
        any(sentence in chunk_text for chunk_text in candidate_texts)
        for sentence in sentences
    )


def _source_name(value: Any) -> str:
    """Return a filename from either POSIX or Windows source paths."""

    if not isinstance(value, str):
        return ""
    return PureWindowsPath(value).name or Path(value).name


def _resolve_source_root(dataset_path: Path | None, source_root: Any) -> Path | None:
    if not _non_empty_string(source_root):
        return None

    root = Path(source_root)
    if root.is_absolute():
        return root

    candidates: list[Path] = []
    if dataset_path is not None:
        parent = dataset_path.resolve().parent
        candidates.extend([parent / root, parent.parent / root, parent.parent.parent / root])
    candidates.append(Path.cwd() / root)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else root


def _discover_chroma_path(dataset_path: Path | None) -> Path | None:
    candidates: list[Path] = []
    if dataset_path is not None:
        current = dataset_path.resolve().parent
        for _ in range(5):
            candidates.append(current / "data" / "db" / "chroma" / "chroma.sqlite3")
            current = current.parent
    candidates.append(Path.cwd() / "data" / "db" / "chroma" / "chroma.sqlite3")

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_chroma_chunk_metadata(
    sqlite_path: str | Path,
    collection: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Read chunk IDs, source metadata, and text from Chroma's SQLite store.

    Chroma stores document text and metadata in the metadata segment.  Reading
    this small, read-only representation keeps the validator usable in tests
    and avoids importing the application's optional retrieval dependencies.
    """

    path = Path(sqlite_path)
    if not path.is_file():
        raise FileNotFoundError(f"Chunk storage not found: {path}")

    query = """
        SELECT e.embedding_id, m.key, m.string_value, m.int_value,
               m.float_value, m.bool_value
        FROM embeddings AS e
        JOIN embedding_metadata AS m ON e.id = m.id
        JOIN segments AS s ON e.segment_id = s.id
        JOIN collections AS c ON s.collection = c.id
        WHERE s.scope = 'METADATA'
    """
    params: tuple[Any, ...] = ()
    if collection:
        query += " AND c.name = ?"
        params = (collection,)

    chunks: dict[str, dict[str, Any]] = {}
    with sqlite3.connect(path) as connection:
        rows = connection.execute(query, params).fetchall()

    for chunk_id, key, string_value, int_value, float_value, bool_value in rows:
        value = string_value
        if value is None:
            value = int_value if int_value is not None else float_value
        if value is None:
            value = bool_value
        chunks.setdefault(str(chunk_id), {})[str(key)] = value

    for metadata in chunks.values():
        if "source_path" in metadata:
            metadata["source"] = _source_name(metadata["source_path"])
        if "chroma:document" in metadata:
            metadata["text"] = metadata["chroma:document"]
    return chunks


def _validate_case(
    case: Any,
    index: int,
    seen_ids: set[str],
    result: ValidationResult,
    source_root: Path | None,
    chunk_metadata: Mapping[str, Mapping[str, Any]] | None,
) -> bool:
    prefix = f"test_cases[{index}]"
    if not isinstance(case, dict):
        result.errors.append(f"{prefix}: must be an object")
        return False

    case_valid = True
    case_id = case.get("id")
    if not _non_empty_string(case_id):
        result.errors.append(f"{prefix}.id: must be a non-empty string")
        case_valid = False
    elif case_id in seen_ids:
        result.errors.append(f"{prefix}.id: duplicate id '{case_id}'")
        case_valid = False
    else:
        seen_ids.add(case_id)

    for field_name in ("query", "reference_answer"):
        if not _non_empty_string(case.get(field_name)):
            result.errors.append(f"{prefix}.{field_name}: must be non-empty")
            case_valid = False

    evidence = case.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        result.errors.append(f"{prefix}.evidence: must be a non-empty list")
        case_valid = False
        evidence = []

    expected_sources = case.get("expected_sources")
    if not isinstance(expected_sources, list) or not expected_sources:
        result.errors.append(f"{prefix}.expected_sources: must be a non-empty list")
        case_valid = False
        expected_sources = []

    expected_source_set = {
        source for source in expected_sources if isinstance(source, str)
    }
    for evidence_index, item in enumerate(evidence):
        evidence_prefix = f"{prefix}.evidence[{evidence_index}]"
        if not isinstance(item, dict):
            result.errors.append(f"{evidence_prefix}: must be an object")
            case_valid = False
            continue
        if not _non_empty_string(item.get("source")):
            result.errors.append(f"{evidence_prefix}.source: must be non-empty")
            case_valid = False
        elif item["source"] not in expected_source_set:
            result.errors.append(
                f"{evidence_prefix}.source: '{item['source']}' is not in expected_sources"
            )
            case_valid = False
        if not _non_empty_string(item.get("text")):
            result.errors.append(f"{evidence_prefix}.text: must be non-empty")
            case_valid = False

    for source in expected_sources:
        if not _non_empty_string(source):
            result.errors.append(f"{prefix}.expected_sources: contains an empty source")
            case_valid = False
        elif source_root is not None and not (source_root / source).is_file():
            result.errors.append(
                f"{prefix}.expected_sources: source file does not exist: {source}"
            )
            case_valid = False
        result.sources[str(source)] += 1

    category = case.get("category")
    if category not in VALID_CATEGORIES:
        result.errors.append(f"{prefix}.category: invalid value '{category}'")
        case_valid = False
    else:
        result.categories[category] += 1

    difficulty = case.get("difficulty")
    if difficulty not in VALID_DIFFICULTIES:
        result.errors.append(f"{prefix}.difficulty: invalid value '{difficulty}'")
        case_valid = False
    else:
        result.difficulty[difficulty] += 1

    review_status = case.get("review_status")
    if review_status not in VALID_REVIEW_STATUSES:
        result.errors.append(f"{prefix}.review_status: invalid value '{review_status}'")
        case_valid = False
    else:
        result.review_status[review_status] += 1

    expected_chunk_ids = case.get("expected_chunk_ids")
    if not isinstance(expected_chunk_ids, list):
        result.errors.append(f"{prefix}.expected_chunk_ids: must be a list")
        case_valid = False
        expected_chunk_ids = []
    seen_chunk_ids: list[Any] = []
    for chunk_id in expected_chunk_ids:
        if not _non_empty_string(chunk_id):
            result.errors.append(
                f"{prefix}.expected_chunk_ids: every ID must be a non-empty string"
            )
            case_valid = False
        elif chunk_id in seen_chunk_ids:
            result.errors.append(f"{prefix}.expected_chunk_ids: contains duplicate IDs")
            case_valid = False
        else:
            seen_chunk_ids.append(chunk_id)

    if review_status != "unresolved" and not expected_chunk_ids:
        result.errors.append(
            f"{prefix}.expected_chunk_ids: required when review_status is '{review_status}'"
        )
        case_valid = False

    if chunk_metadata is not None:
        missing_ids = [
            chunk_id
            for chunk_id in expected_chunk_ids
            if _non_empty_string(chunk_id) and chunk_id not in chunk_metadata
        ]
        if missing_ids:
            result.errors.append(
                f"{prefix}.expected_chunk_ids: unknown chunk ID(s): {', '.join(missing_ids)}"
            )
            case_valid = False

        for chunk_id in expected_chunk_ids:
            if not _non_empty_string(chunk_id):
                continue
            metadata = chunk_metadata.get(chunk_id)
            if metadata is None:
                continue
            chunk_source = metadata.get("source") or _source_name(metadata.get("source_path"))
            if chunk_source and chunk_source not in expected_source_set:
                result.errors.append(
                    f"{prefix}.expected_chunk_ids: chunk '{chunk_id}' maps to '{chunk_source}', "
                    "which is not in expected_sources"
                )
                case_valid = False

        # An unresolved case may intentionally have no reliable chunk mapping.
        # Once expected chunks are supplied, evidence must be traceable to them.
        if expected_chunk_ids:
            for evidence_index, item in enumerate(evidence):
                if not isinstance(item, dict):
                    continue
                evidence_source = item.get("source")
                evidence_text = item.get("text")
                if not _non_empty_string(evidence_source) or not _non_empty_string(evidence_text):
                    continue

                candidate_texts = []
                for chunk_id in expected_chunk_ids:
                    if not _non_empty_string(chunk_id):
                        continue
                    metadata = chunk_metadata.get(chunk_id)
                    if metadata is None:
                        continue
                    chunk_source = metadata.get("source") or _source_name(
                        metadata.get("source_path")
                    )
                    if chunk_source != evidence_source:
                        continue
                    chunk_text = metadata.get("text")
                    if isinstance(chunk_text, str):
                        candidate_texts.append(chunk_text)

                if not _evidence_supported_by_chunks(evidence_text, candidate_texts):
                    evidence_label = f"{case_id or prefix}.evidence[{evidence_index}]"
                    result.errors.append(
                        f"{evidence_label}: evidence is not supported by expected_chunk_ids "
                        f"for source '{evidence_source}' "
                        f"(expected_chunk_ids: {', '.join(expected_chunk_ids)})"
                    )
                    case_valid = False

    return case_valid


def validate_dataset(
    data: Mapping[str, Any],
    *,
    dataset_path: str | Path | None = None,
    chunk_metadata: Mapping[str, Mapping[str, Any]] | None = None,
) -> ValidationResult:
    """Validate an in-memory Golden Set.

    ``chunk_metadata`` is injectable so unit tests can exercise chunk
    existence/source checks with a tiny fixture instead of a real database.
    """

    dataset_file = Path(dataset_path) if dataset_path is not None else None
    result = ValidationResult(dataset_name=str(data.get("name", "")))
    source_root = _resolve_source_root(dataset_file, data.get("source_root"))

    cases = data.get("test_cases")
    if not isinstance(cases, list) or not cases:
        result.errors.append("test_cases: must exist and be non-empty")
        return result

    result.total_cases = len(cases)
    seen_ids: set[str] = set()
    for index, case in enumerate(cases):
        if _validate_case(case, index, seen_ids, result, source_root, chunk_metadata):
            result.valid_cases += 1

    if chunk_metadata is not None:
        result.chunk_storage_available = True
        result.resolved_chunk_cases = sum(
            bool(case.get("expected_chunk_ids"))
            and all(
                _non_empty_string(chunk_id) and chunk_id in chunk_metadata
                for chunk_id in case.get("expected_chunk_ids", [])
            )
            for case in cases
            if isinstance(case, dict)
        )
        result.unresolved_chunk_cases = result.total_cases - result.resolved_chunk_cases
    else:
        result.warnings.append(
            "Chunk storage was not available; expected_chunk_ids existence was not checked."
        )
        result.unresolved_chunk_cases = sum(
            not case.get("expected_chunk_ids")
            for case in cases
            if isinstance(case, dict)
        )

    return result


def validate_file(
    test_set_path: str | Path,
    *,
    chunk_storage_path: str | Path | None = None,
    collection: str | None = None,
    skip_chunk_storage: bool = False,
) -> ValidationResult:
    """Load and validate a dataset file."""

    path = Path(test_set_path)
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        result = ValidationResult()
        result.errors.append(f"Could not read dataset {path}: {exc}")
        return result

    if not isinstance(data, dict):
        result = ValidationResult()
        result.errors.append("Dataset root must be a JSON object")
        return result

    chunk_metadata = None
    if not skip_chunk_storage:
        storage = Path(chunk_storage_path) if chunk_storage_path else _discover_chroma_path(path)
        if storage is not None:
            try:
                chunk_metadata = load_chroma_chunk_metadata(
                    storage,
                    collection=collection or DEFAULT_COLLECTION,
                )
            except (OSError, sqlite3.Error) as exc:
                result = ValidationResult(dataset_name=str(data.get("name", "")))
                result.errors.append(f"Could not read chunk storage {storage}: {exc}")
                return result

    return validate_dataset(data, dataset_path=path, chunk_metadata=chunk_metadata)


def format_report(result: ValidationResult) -> str:
    """Render a stable human-readable validation report."""

    lines = [
        "Golden Set Validation",
        "-" * 50,
        f"Dataset: {result.dataset_name or '(unnamed)'}",
        f"Cases: {result.total_cases}",
        f"Valid: {result.valid_cases}",
        f"Invalid: {result.total_cases - result.valid_cases + (1 if result.total_cases == 0 and result.errors else 0)}",
        "",
        "Review Status:",
    ]
    for key in sorted(VALID_REVIEW_STATUSES):
        lines.append(f"  {key.capitalize()}: {result.review_status.get(key, 0)}")
    lines.append("\nDifficulty:")
    for key in ("easy", "medium", "hard"):
        lines.append(f"  {key.capitalize()}: {result.difficulty.get(key, 0)}")
    lines.append("\nCategories:")
    for key in sorted(result.categories):
        lines.append(f"  {key}: {result.categories[key]}")
    lines.append("\nSources Covered:")
    for key in sorted(result.sources):
        lines.append(f"  {key}: {result.sources[key]}")
    lines.extend(
        [
            "",
            "Chunk Mapping:",
            f"  Resolved: {result.resolved_chunk_cases}",
            f"  Unresolved: {result.unresolved_chunk_cases}",
        ]
    )
    if result.warnings:
        lines.append("\nWarnings:")
        lines.extend(f"  - {warning}" for warning in result.warnings)
    if result.errors:
        lines.append("\nErrors:")
        lines.extend(f"  - {error}" for error in result.errors)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a RAG Golden Set JSON file.")
    parser.add_argument("--test-set", required=True, help="Path to the Golden Set JSON file")
    parser.add_argument(
        "--chunk-storage",
        help="Optional Chroma SQLite path (defaults to auto-discovery under data/db/chroma)",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help=f"Chroma collection to inspect (default: {DEFAULT_COLLECTION})",
    )
    parser.add_argument(
        "--skip-chunk-storage",
        action="store_true",
        help="Skip persisted chunk existence/source checks",
    )
    args = parser.parse_args(argv)

    result = validate_file(
        args.test_set,
        chunk_storage_path=args.chunk_storage,
        collection=args.collection,
        skip_chunk_storage=args.skip_chunk_storage,
    )
    print(format_report(result))
    return 0 if result.is_valid else 1


if __name__ == "__main__":
    sys.exit(main())
