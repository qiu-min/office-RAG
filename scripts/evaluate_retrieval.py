#!/usr/bin/env python
"""Run the deterministic Retrieval Evaluation Baseline.

This command evaluates only retrieval against reviewed golden cases. It uses
the production HybridSearch and the reranker configured in settings.yaml; it
does not generate answers or invoke Ragas.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_TEST_SET = "evals/datasets/business_golden_v1.json"
DEFAULT_COLLECTION = "ubuntu_ops_filtered"
DEFAULT_TOP_K = 10
DEFAULT_KS = [1, 3, 5, 10]
DEFAULT_OUTPUT = "evals/reports/retrieval_baseline_v1.json"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate production retrieval against a reviewed golden set."
    )
    parser.add_argument("--test-set", default=DEFAULT_TEST_SET)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--ks", type=int, nargs="+", default=DEFAULT_KS)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def _validate_args(args: argparse.Namespace) -> None:
    if args.top_k <= 0:
        raise ValueError("--top-k must be greater than 0")
    if not args.ks:
        raise ValueError("--ks must contain at least one positive integer")
    if any(k <= 0 for k in args.ks):
        raise ValueError("all --ks values must be greater than 0")
    if any(k > args.top_k for k in args.ks):
        raise ValueError("every --ks value must be less than or equal to --top-k")
    if len(set(args.ks)) != len(args.ks):
        raise ValueError("--ks values must be unique")


def _build_retrieval_pipeline(settings: Any, collection: str):
    """Build the same production retrieval components used by scripts/query.py."""
    from src.core.query_engine.dense_retriever import create_dense_retriever
    from src.core.query_engine.hybrid_search import create_hybrid_search
    from src.core.query_engine.query_processor import QueryProcessor
    from src.core.query_engine.reranker import create_core_reranker
    from src.core.query_engine.sparse_retriever import create_sparse_retriever
    from src.core.settings import resolve_path
    from src.ingestion.storage.bm25_indexer import BM25Indexer
    from src.libs.embedding.embedding_factory import EmbeddingFactory
    from src.libs.vector_store.vector_store_factory import VectorStoreFactory

    vector_store = VectorStoreFactory.create(
        settings,
        collection_name=collection,
    )
    _verify_collection(vector_store, collection)

    embedding_client = EmbeddingFactory.create(settings)
    dense_retriever = create_dense_retriever(
        settings=settings,
        embedding_client=embedding_client,
        vector_store=vector_store,
    )

    bm25_indexer = BM25Indexer(
        index_dir=str(resolve_path(f"data/db/bm25/{collection}"))
    )
    if not bm25_indexer.load(collection):
        raise RuntimeError(
            f"BM25 index for collection '{collection}' was not found or could not be loaded"
        )
    sparse_retriever = create_sparse_retriever(
        settings=settings,
        bm25_indexer=bm25_indexer,
        vector_store=vector_store,
    )
    sparse_retriever.default_collection = collection

    hybrid_search = create_hybrid_search(
        settings=settings,
        query_processor=QueryProcessor(),
        dense_retriever=dense_retriever,
        sparse_retriever=sparse_retriever,
    )
    if hybrid_search is None:
        raise RuntimeError("HybridSearch initialization returned no pipeline")

    reranker = create_core_reranker(settings=settings)
    configured_reranker_enabled = bool(getattr(settings.rerank, "enabled", False))
    if configured_reranker_enabled and not getattr(reranker, "is_enabled", False):
        raise RuntimeError(
            "Reranker is enabled in settings but the configured reranker could not be initialized"
        )

    return hybrid_search, reranker


def _verify_collection(vector_store: Any, collection: str) -> None:
    """Fail early for an unavailable or empty production collection."""
    store_collection = getattr(vector_store, "collection", None)
    if store_collection is None or not hasattr(store_collection, "count"):
        raise RuntimeError(
            f"Collection '{collection}' is not readable through the configured vector store"
        )
    try:
        count = int(store_collection.count())
    except Exception as exc:
        raise RuntimeError(f"Collection '{collection}' cannot be read: {exc}") from exc
    if count <= 0:
        raise RuntimeError(
            f"Collection '{collection}' is unavailable or contains no indexed chunks"
        )


def _metric_names(ks: Sequence[int]) -> list[str]:
    return [*(f"hit_rate@{k}" for k in ks), *(f"recall@{k}" for k in ks), "mrr"]


def _build_report(
    report: Any,
    args: argparse.Namespace,
    reranker: Any,
) -> dict[str, Any]:
    report_dict = report.to_dict()
    reranker_enabled = bool(getattr(reranker, "is_enabled", False))
    metric_names = _metric_names(args.ks)
    return {
        "name": "retrieval_baseline_v1",
        "version": "1.0",
        "test_set": str(args.test_set),
        "collection": args.collection,
        "top_k": args.top_k,
        "ks": list(args.ks),
        "query_count": len(report.query_results),
        "aggregate_metrics": {
            name: round(float(report.aggregate_metrics.get(name, 0.0)), 4)
            for name in metric_names
        },
        "query_results": report_dict["query_results"],
        "metadata": {
            "evaluator": "CustomEvaluator",
            "retrieval_pipeline": (
                "HybridSearch + Reranker" if reranker_enabled else "HybridSearch only"
            ),
            "reranker_enabled": reranker_enabled,
            "reranker_type": getattr(reranker, "reranker_type", "none"),
        },
    }


def _write_report_atomic(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _print_summary(report: dict[str, Any]) -> None:
    print("Retrieval Baseline v1")
    print("-" * 50)
    print(f"Dataset: {Path(report['test_set']).stem}")
    print(f"Collection: {report['collection']}")
    print(f"Cases evaluated: {report['query_count']}")
    print(f"Top-K: {report['top_k']}")
    print()
    for metric, value in report["aggregate_metrics"].items():
        label = metric.replace("hit_rate", "HitRate").replace("recall", "Recall")
        if metric == "mrr":
            label = "MRR"
        print(f"{label}: {value:.4f}")
    print()
    print(f"Reranker: {report['metadata']['retrieval_pipeline']}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        _validate_args(args)
        from src.core.settings import load_settings
        from src.libs.evaluator.custom_evaluator import CustomEvaluator
        from src.observability.evaluation.eval_runner import EvalRunner, load_test_set

        test_cases = load_test_set(args.test_set)
        has_review_status = any(tc.review_status is not None for tc in test_cases)
        eligible_cases = (
            [tc for tc in test_cases if tc.review_status == "reviewed"]
            if has_review_status
            else test_cases
        )
        if not eligible_cases:
            raise ValueError("Golden Set has no valid reviewed cases")

        settings = load_settings()
        hybrid_search, reranker = _build_retrieval_pipeline(settings, args.collection)
        evaluator = CustomEvaluator(metrics=_metric_names(args.ks))
        runner = EvalRunner(
            settings=settings,
            hybrid_search=hybrid_search,
            evaluator=evaluator,
            reranker=reranker,
        )
        evaluation = runner.run(
            test_set_path=args.test_set,
            top_k=args.top_k,
            collection=args.collection,
            retrieval_only=True,
            reviewed_only=True,
            strict_retrieval=True,
        )
        output_report = _build_report(evaluation, args, reranker)
        _write_report_atomic(Path(args.output), output_report)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Baseline configuration/data error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Baseline failed: {exc}", file=sys.stderr)
        return 1

    _print_summary(output_report)
    print(f"Report: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
