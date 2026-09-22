"""Unit tests for Retrieval Baseline report integrity checks."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.evaluate_retrieval import (
    _build_report,
    _write_report_atomic,
)
from src.observability.evaluation.eval_runner import EvalReport, QueryResult


KS = [1, 3]
EXPECTED = {
    "hit_rate@1": 1.0,
    "hit_rate@3": 1.0,
    "recall@1": 0.5,
    "recall@3": 1.0,
    "mrr": 1.0,
}


def _args() -> Namespace:
    return Namespace(
        test_set="fixture.json",
        collection="fixture_collection",
        top_k=3,
        ks=KS,
        output="report.json",
    )


def _report(
    case_metrics: dict[str, float] | None = None,
    aggregate_metrics: dict[str, float] | None = None,
) -> EvalReport:
    metrics = case_metrics if case_metrics is not None else dict(EXPECTED)
    aggregate = aggregate_metrics if aggregate_metrics is not None else dict(EXPECTED)
    return EvalReport(
        query_results=[
            QueryResult(
                query="Q",
                case_id="case_001",
                metrics=metrics,
            )
        ],
        aggregate_metrics=aggregate,
    )


def _reranker() -> SimpleNamespace:
    return SimpleNamespace(is_enabled=False, reranker_type="none")


def test_complete_metrics_build_report() -> None:
    report = _build_report(_report(), _args(), _reranker())

    assert report["aggregate_metrics"] == EXPECTED


def test_missing_case_metric_fails_validation() -> None:
    metrics = dict(EXPECTED)
    del metrics["recall@3"]

    with pytest.raises(RuntimeError, match="case_001.*recall@3"):
        _build_report(_report(case_metrics=metrics), _args(), _reranker())


def test_missing_aggregate_metric_fails_validation() -> None:
    aggregate = dict(EXPECTED)
    del aggregate["mrr"]

    with pytest.raises(RuntimeError, match="aggregate.*mrr"):
        _build_report(_report(aggregate_metrics=aggregate), _args(), _reranker())


def test_complete_report_can_be_written(tmp_path: Path) -> None:
    path = tmp_path / "reports" / "retrieval_baseline_v1.json"
    report = _build_report(_report(), _args(), _reranker())

    _write_report_atomic(path, report)

    assert path.exists()
    assert path.read_text(encoding="utf-8").startswith("{")


def test_validation_failure_does_not_write_baseline_file(tmp_path: Path) -> None:
    path = tmp_path / "retrieval_baseline_v1.json"
    metrics = dict(EXPECTED)
    del metrics["mrr"]

    with pytest.raises(RuntimeError):
        _build_report(_report(case_metrics=metrics), _args(), _reranker())
    assert not path.exists()
