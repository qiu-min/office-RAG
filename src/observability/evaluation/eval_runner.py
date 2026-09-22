"""Evaluation runner for batch quality assessment.

EvalRunner reads a golden test set, runs HybridSearch for each test case,
optionally generates answers, then invokes the configured Evaluator(s) to
produce a structured evaluation report.

Design Principles:
- Config-Driven: Evaluator selected via settings.yaml.
- Observable: Produces EvalReport with per-query details.
- Decoupled: Works with any BaseEvaluator implementation.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.libs.evaluator.base_evaluator import BaseEvaluator

logger = logging.getLogger(__name__)


@dataclass
class GoldenTestCase:
    """A single evaluation test case from the golden test set.

    Attributes:
        query: The test query string.
        expected_chunk_ids: Ground-truth chunk IDs for IR metrics.
        expected_sources: Ground-truth source file names (optional).
        reference_answer: Reference answer text for LLM-as-Judge (optional).
        id: Stable test case identifier (optional for legacy fixtures).
        category: Business category (optional).
        difficulty: Business difficulty (optional).
        review_status: Golden-set review state (optional for legacy fixtures).
    """

    query: str
    expected_chunk_ids: List[str] = field(default_factory=list)
    expected_sources: List[str] = field(default_factory=list)
    reference_answer: Optional[str] = None
    id: Optional[str] = None
    category: Optional[str] = None
    difficulty: Optional[str] = None
    review_status: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GoldenTestCase:
        return cls(
            query=data["query"],
            expected_chunk_ids=data.get("expected_chunk_ids", []),
            expected_sources=data.get("expected_sources", []),
            reference_answer=data.get("reference_answer"),
            id=data.get("id"),
            category=data.get("category"),
            difficulty=data.get("difficulty"),
            review_status=data.get("review_status"),
        )


@dataclass
class QueryResult:
    """Result of evaluating a single test case.

    Attributes:
        query: The test query.
        retrieved_chunk_ids: IDs of chunks actually retrieved.
        case_id: Stable test case identifier when supplied by the fixture.
        expected_chunk_ids: Ground-truth chunk IDs used for retrieval metrics.
        category: Optional business category.
        difficulty: Optional business difficulty.
        generated_answer: The generated answer (if applicable).
        metrics: Evaluation metrics for this query.
        elapsed_ms: Time taken for retrieval + evaluation.
    """

    # Keep the original positional field order for compatibility with callers
    # that construct QueryResult(query, retrieved_ids, answer, metrics, ms).
    query: str
    retrieved_chunk_ids: List[str] = field(default_factory=list)
    generated_answer: Optional[str] = None
    metrics: Dict[str, float] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    case_id: Optional[str] = None
    expected_chunk_ids: List[str] = field(default_factory=list)
    category: Optional[str] = None
    difficulty: Optional[str] = None


@dataclass
class EvalReport:
    """Aggregated evaluation report across all test cases.

    Attributes:
        query_results: Per-query evaluation results.
        aggregate_metrics: Averaged metrics across all queries.
        total_elapsed_ms: Total time for the entire evaluation.
        evaluator_name: Name of the evaluator used.
        test_set_path: Path to the golden test set file.
    """

    query_results: List[QueryResult] = field(default_factory=list)
    aggregate_metrics: Dict[str, float] = field(default_factory=dict)
    total_elapsed_ms: float = 0.0
    evaluator_name: str = ""
    test_set_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialise report to dictionary."""
        return {
            "evaluator_name": self.evaluator_name,
            "test_set_path": self.test_set_path,
            "total_elapsed_ms": round(self.total_elapsed_ms, 1),
            "aggregate_metrics": {
                k: round(v, 4) for k, v in self.aggregate_metrics.items()
            },
            "query_count": len(self.query_results),
            "query_results": [
                {
                    "query": qr.query,
                    "case_id": qr.case_id,
                    "expected_chunk_ids": qr.expected_chunk_ids,
                    "retrieved_chunk_ids": qr.retrieved_chunk_ids,
                    "category": qr.category,
                    "difficulty": qr.difficulty,
                    "generated_answer": qr.generated_answer,
                    "metrics": {k: round(v, 4) for k, v in qr.metrics.items()},
                    "elapsed_ms": round(qr.elapsed_ms, 1),
                }
                for qr in self.query_results
            ],
        }


def load_test_set(path: str | Path) -> List[GoldenTestCase]:
    """Load golden test set from a JSON file.

    Args:
        path: Path to the golden test set JSON file.

    Returns:
        List of TestCase instances.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file format is invalid.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Golden test set not found: {file_path}")

    with file_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if "test_cases" not in data:
        raise ValueError(
            "Invalid golden test set format: missing 'test_cases' key."
        )

    return [GoldenTestCase.from_dict(tc) for tc in data["test_cases"]]


class EvalRunner:
    """Runs batch evaluation against a golden test set.

    This class orchestrates:
    1. Loading the golden test set
    2. Running HybridSearch for each query
    3. Optionally generating answers
    4. Invoking the evaluator to score each result
    5. Aggregating metrics into an EvalReport

    Example::

        runner = EvalRunner(
            settings=settings,
            hybrid_search=hybrid_search,
            evaluator=evaluator,
        )
        report = runner.run("tests/fixtures/golden_test_set.json")
        print(report.aggregate_metrics)
    """

    def __init__(
        self,
        settings: Any = None,
        hybrid_search: Any = None,
        evaluator: Optional[BaseEvaluator] = None,
        answer_generator: Any = None,
        answer_overrides: Optional[Dict[int, str]] = None,
        reranker: Any = None,
    ) -> None:
        """Initialize EvalRunner.

        Args:
            settings: Application settings.
            hybrid_search: HybridSearch instance for retrieval.
            evaluator: BaseEvaluator instance for scoring.
            answer_generator: Optional callable(query, chunks) -> str
                for generating answers. If None, a simple concatenation
                is used as a placeholder.
            answer_overrides: Optional dict mapping test case index (0-based)
                to a user-provided answer string. When present, the override
                answer is used instead of auto-generation for that test case.
            reranker: Optional CoreReranker instance for reranking results.
        """
        self.settings = settings
        self.hybrid_search = hybrid_search
        self.evaluator = evaluator
        self.answer_generator = answer_generator
        self.answer_overrides = answer_overrides or {}
        self.reranker = reranker

    def run(
        self,
        test_set_path: str | Path,
        top_k: int = 10,
        collection: Optional[str] = None,
        retrieval_only: bool = False,
        reviewed_only: bool = False,
        strict_retrieval: bool = False,
        strict_evaluation: bool = False,
    ) -> EvalReport:
        """Run evaluation on the golden test set.

        Args:
            test_set_path: Path to golden_test_set.json.
            top_k: Number of chunks to retrieve per query.
            collection: Optional collection name filter.
            retrieval_only: Skip answer generation and evaluate retrieved chunks only.
            reviewed_only: Evaluate reviewed cases when review metadata is present;
                preserve compatibility with fixtures that have no review metadata.
            strict_retrieval: Raise on missing or failed retrieval instead of
                converting the failure into an empty result.
            strict_evaluation: Raise on evaluator failures instead of omitting
                the case metrics from aggregation.

        Returns:
            EvalReport with per-query and aggregate metrics.

        Raises:
            FileNotFoundError: If test set file doesn't exist.
            ValueError: If evaluator or hybrid_search is not set.
        """
        if self.evaluator is None:
            raise ValueError("EvalRunner requires an evaluator.")

        test_cases = load_test_set(test_set_path)
        if reviewed_only:
            has_review_status = any(tc.review_status is not None for tc in test_cases)
            if has_review_status:
                test_cases = [tc for tc in test_cases if tc.review_status == "reviewed"]
        if not test_cases:
            if reviewed_only:
                raise ValueError("Golden test set has no valid reviewed cases.")
            raise ValueError("Golden test set is empty.")

        logger.info(
            "Starting evaluation: %d test cases, evaluator=%s",
            len(test_cases),
            type(self.evaluator).__name__,
        )

        report = EvalReport(
            evaluator_name=type(self.evaluator).__name__,
            test_set_path=str(test_set_path),
        )

        t0 = time.monotonic()

        for idx, tc in enumerate(test_cases):
            logger.info("Evaluating [%d/%d]: %s", idx + 1, len(test_cases), tc.query[:60])
            # Use user-provided answer override if available for this index
            answer_override = self.answer_overrides.get(idx)
            qr = self._evaluate_single(
                tc, top_k=top_k, collection=collection,
                answer_override=answer_override,
                retrieval_only=retrieval_only,
                strict_retrieval=strict_retrieval,
                strict_evaluation=strict_evaluation,
            )
            report.query_results.append(qr)

        report.total_elapsed_ms = max((time.monotonic() - t0) * 1000.0, 0.001)
        report.aggregate_metrics = self._aggregate_metrics(report.query_results)

        logger.info(
            "Evaluation complete: %d queries, aggregate=%s",
            len(report.query_results),
            report.aggregate_metrics,
        )

        return report

    def _evaluate_single(
        self,
        test_case: GoldenTestCase,
        top_k: int = 10,
        collection: Optional[str] = None,
        answer_override: Optional[str] = None,
        retrieval_only: bool = False,
        strict_retrieval: bool = False,
        strict_evaluation: bool = False,
    ) -> QueryResult:
        """Evaluate a single test case.

        Args:
            test_case: The test case to evaluate.
            top_k: Number of results to retrieve.
            collection: Optional collection filter.
            answer_override: User-provided answer text. When set, used
                instead of auto-generated answer from chunks.

        Returns:
            QueryResult with metrics for this test case.
        """
        t0 = time.monotonic()
        qr = QueryResult(
            query=test_case.query,
            case_id=test_case.id,
            expected_chunk_ids=list(test_case.expected_chunk_ids),
            category=test_case.category,
            difficulty=test_case.difficulty,
        )

        # Step 1: Retrieve chunks
        retrieved_chunks = self._retrieve(
            test_case.query, top_k, collection, strict=strict_retrieval
        )
        qr.retrieved_chunk_ids = [
            self._get_chunk_id(c) for c in retrieved_chunks
        ]

        # Step 2: Generate answer — prefer user override, then generator, then fallback
        if retrieval_only:
            answer = None
        elif answer_override:
            answer = answer_override
        else:
            answer = self._generate_answer(test_case.query, retrieved_chunks)
        qr.generated_answer = answer

        # Step 3: Build ground truth
        ground_truth = (
            {"ids": test_case.expected_chunk_ids}
            if test_case.expected_chunk_ids
            else None
        )

        # Step 4: Evaluate
        try:
            evaluate_kwargs = {
                "query": test_case.query,
                "retrieved_chunks": retrieved_chunks,
                "generated_answer": answer,
                "ground_truth": ground_truth,
            }
            if retrieval_only:
                evaluate_kwargs["allow_empty_retrieval"] = True
            metrics = self.evaluator.evaluate(  # type: ignore[union-attr]
                **evaluate_kwargs,
            )
            qr.metrics = metrics
        except Exception as exc:
            if strict_evaluation:
                raise RuntimeError(
                    f"Evaluation failed for '{test_case.query[:40]}': {exc}"
                ) from exc
            logger.warning("Evaluation failed for '%s': %s", test_case.query[:40], exc)
            qr.metrics = {}

        qr.elapsed_ms = (time.monotonic() - t0) * 1000.0
        return qr

    def _retrieve(
        self,
        query: str,
        top_k: int,
        collection: Optional[str],
        strict: bool = False,
    ) -> List[Any]:
        """Retrieve chunks using HybridSearch + optional Reranking.

        Falls back to an empty list if search is not configured.
        """
        if self.hybrid_search is None:
            if strict:
                raise RuntimeError("No HybridSearch configured for retrieval evaluation")
            logger.warning("No HybridSearch configured; returning empty results.")
            return []

        try:
            # Retrieve more candidates if reranker is enabled
            has_reranker = self.reranker is not None and getattr(self.reranker, 'is_enabled', False)
            initial_top_k = top_k * 2 if has_reranker else top_k

            if strict:
                search_result = self.hybrid_search.search(
                    query=query,
                    top_k=initial_top_k,
                    return_details=True,
                )
                if getattr(search_result, "used_fallback", False):
                    raise RuntimeError(
                        "HybridSearch fallback occurred during strict retrieval baseline: "
                        f"query='{query}' "
                        f"dense_error='{getattr(search_result, 'dense_error', None)}' "
                        f"sparse_error='{getattr(search_result, 'sparse_error', None)}'"
                    )
                if not hasattr(search_result, "results"):
                    raise RuntimeError(
                        "Strict retrieval baseline expected HybridSearch details"
                    )
                results = search_result.results
            else:
                results = self.hybrid_search.search(
                    query=query,
                    top_k=initial_top_k,
                )
                results = results if isinstance(results, list) else results.results
            # Apply reranking if enabled
            if has_reranker and results:
                rerank_result = self.reranker.rerank(query=query, results=results, top_k=top_k)
                if strict and getattr(rerank_result, "used_fallback", False):
                    raise RuntimeError(
                        "Reranker fallback occurred during strict retrieval baseline: "
                        f"query='{query}' "
                        f"reranker='{getattr(rerank_result, 'reranker_type', 'unknown')}' "
                        f"reason='{getattr(rerank_result, 'fallback_reason', None)}'"
                    )
                results = rerank_result.results

            return results
        except Exception as exc:
            if strict:
                raise RuntimeError(f"Retrieval failed for '{query[:40]}': {exc}") from exc
            logger.warning("Retrieval failed for '%s': %s", query[:40], exc)
            return []

    def _generate_answer(self, query: str, chunks: List[Any]) -> str:
        """Generate an answer from retrieved chunks.

        If a custom answer_generator is provided, use it.
        Otherwise, concatenate chunk texts as a simple placeholder.
        """
        if self.answer_generator is not None:
            try:
                return self.answer_generator(query, chunks)
            except Exception as exc:
                logger.warning("Answer generation failed: %s", exc)

        # Fallback: concatenate chunk texts
        texts = []
        for c in chunks:
            if isinstance(c, str):
                texts.append(c)
            elif isinstance(c, dict):
                texts.append(c.get("text", str(c)))
            elif hasattr(c, "text"):
                texts.append(str(getattr(c, "text")))
            else:
                texts.append(str(c))

        return " ".join(texts[:5])  # first 5 chunks

    def _get_chunk_id(self, chunk: Any) -> str:
        """Extract chunk ID from various representations."""
        if isinstance(chunk, str):
            return chunk
        if isinstance(chunk, dict):
            for key in ("id", "chunk_id"):
                if key in chunk:
                    return str(chunk[key])
            return str(chunk)
        if hasattr(chunk, "chunk_id"):
            return str(getattr(chunk, "chunk_id"))
        if hasattr(chunk, "id"):
            return str(getattr(chunk, "id"))
        return str(chunk)

    @staticmethod
    def _aggregate_metrics(results: List[QueryResult]) -> Dict[str, float]:
        """Compute average metrics across all query results.

        Args:
            results: List of QueryResult with per-query metrics.

        Returns:
            Dictionary of average metric values.
        """
        if not results:
            return {}

        # Collect all metric keys
        all_keys: set[str] = set()
        for qr in results:
            all_keys.update(qr.metrics.keys())

        # Average each metric
        averages: Dict[str, float] = {}
        for key in sorted(all_keys):
            values = [qr.metrics[key] for qr in results if key in qr.metrics]
            averages[key] = sum(values) / len(values) if values else 0.0

        return averages
