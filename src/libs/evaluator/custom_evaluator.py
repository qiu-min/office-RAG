"""Custom evaluator implementation for lightweight metrics.

This evaluator computes simple, deterministic metrics such as hit rate and MRR.
It is designed for fast regression checks and sanity validation.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.libs.evaluator.base_evaluator import BaseEvaluator


class CustomEvaluator(BaseEvaluator):
    """Custom evaluator for lightweight retrieval metrics.

    The evaluator expects retrieved chunks to contain an identifier field.
    Supported id fields: id, chunk_id, document_id, doc_id.
    """

    SUPPORTED_METRICS = {"hit_rate", "mrr"}
    _RANKED_METRIC_PATTERN = re.compile(r"^(hit_rate|recall)@(\d+)$")
    _ID_FIELDS = ("id", "chunk_id", "document_id", "doc_id")

    def __init__(
        self,
        settings: Any = None,
        metrics: Optional[Sequence[str]] = None,
        **kwargs: Any,
    ) -> None:
        self.settings = settings
        self.kwargs = kwargs

        if metrics is None:
            metrics = self._metrics_from_settings(settings)

        normalized = [str(metric).strip().lower() for metric in (metrics or [])]
        if not normalized:
            normalized = ["hit_rate", "mrr"]

        unsupported = [metric for metric in normalized if not self._is_supported_metric(metric)]
        if unsupported:
            raise ValueError(
                "Unsupported custom metrics: "
                f"{', '.join(unsupported)}. Supported: hit_rate, hit_rate@K, recall@K, mrr"
            )

        self.metrics = normalized

    def evaluate(
        self,
        query: str,
        retrieved_chunks: List[Any],
        generated_answer: Optional[str] = None,
        ground_truth: Optional[Any] = None,
        trace: Optional[Any] = None,
        **kwargs: Any,
    ) -> Dict[str, float]:
        """Compute requested metrics for the given retrieval results.

        Args:
            query: The user query string.
            retrieved_chunks: Retrieved chunks or records.
            generated_answer: Optional generated answer (unused).
            ground_truth: Ground truth ids or structure.
            trace: Optional TraceContext (unused).
            **kwargs: Additional parameters (unused).

        Returns:
            Dictionary of metric name to float value.
        """
        self.validate_query(query)
        # Keep the historical validation contract for direct callers.  The
        # retrieval-only runner opts into empty results so a legitimate query
        # miss is scored as zero instead of being confused with an evaluator
        # input error.
        if not (not retrieved_chunks and kwargs.get("allow_empty_retrieval", False)):
            self.validate_retrieved_chunks(retrieved_chunks)

        retrieved_ids = self._extract_ids(retrieved_chunks, label="retrieved_chunks")
        ground_truth_ids = self._extract_ground_truth_ids(ground_truth)

        results: Dict[str, float] = {}

        for metric in self.metrics:
            if metric == "hit_rate":
                results[metric] = self._compute_hit_rate(retrieved_ids, ground_truth_ids)
            elif metric == "mrr":
                results[metric] = self._compute_mrr(retrieved_ids, ground_truth_ids)
            else:
                metric_name, raw_k = metric.rsplit("@", 1)
                k = int(raw_k)
                if metric_name == "hit_rate":
                    results[metric] = self._compute_hit_rate(
                        retrieved_ids[:k], ground_truth_ids
                    )
                else:
                    results[metric] = self._compute_recall(
                        retrieved_ids[:k], ground_truth_ids
                    )

        return results

    def _metrics_from_settings(self, settings: Any) -> List[str]:
        """Extract metrics list from settings if available."""
        if settings is None:
            return []
        metrics = getattr(getattr(settings, "evaluation", None), "metrics", None)
        if metrics is None:
            return []
        return [str(metric) for metric in metrics]

    @classmethod
    def _is_supported_metric(cls, metric: str) -> bool:
        """Return whether *metric* is supported, including a valid @K form."""
        if metric in cls.SUPPORTED_METRICS:
            return True
        match = cls._RANKED_METRIC_PATTERN.fullmatch(metric)
        return bool(match and int(match.group(2)) > 0)

    def _extract_ground_truth_ids(self, ground_truth: Optional[Any]) -> List[str]:
        """Extract ground truth ids from various input shapes."""
        if ground_truth is None:
            return []
        if isinstance(ground_truth, str):
            return [ground_truth]
        if isinstance(ground_truth, dict):
            if "ids" in ground_truth and isinstance(ground_truth["ids"], list):
                return self._extract_ids(ground_truth["ids"], label="ground_truth.ids")
            return self._extract_ids([ground_truth], label="ground_truth")
        if isinstance(ground_truth, list):
            return self._extract_ids(ground_truth, label="ground_truth")

        raise ValueError(
            f"Unsupported ground_truth type: {type(ground_truth).__name__}. "
            "Expected str, dict, list, or None."
        )

    def _extract_ids(self, items: Iterable[Any], label: str) -> List[str]:
        """Extract ids from a list of items."""
        ids: List[str] = []
        for index, item in enumerate(items):
            if isinstance(item, str):
                ids.append(item)
                continue
            if isinstance(item, dict):
                for field in self._ID_FIELDS:
                    if field in item:
                        ids.append(str(item[field]))
                        break
                else:
                    raise ValueError(
                        f"Missing id field in {label}[{index}]. "
                        f"Expected one of {', '.join(self._ID_FIELDS)}"
                    )
                continue
            for field in self._ID_FIELDS:
                if hasattr(item, field):
                    ids.append(str(getattr(item, field)))
                    break
            else:
                raise ValueError(
                    f"Unable to extract id from {label}[{index}] of type "
                    f"{type(item).__name__}"
                )
            continue

        return ids

    def _compute_hit_rate(self, retrieved_ids: Sequence[str], ground_truth_ids: Sequence[str]) -> float:
        """Compute hit rate (binary)."""
        if not ground_truth_ids:
            return 0.0
        return 1.0 if any(item in ground_truth_ids for item in retrieved_ids) else 0.0

    def _compute_recall(
        self,
        retrieved_ids: Sequence[str],
        ground_truth_ids: Sequence[str],
    ) -> float:
        """Compute unique relevant-id recall for a ranked prefix."""
        if not ground_truth_ids:
            return 0.0
        return len(set(retrieved_ids).intersection(ground_truth_ids)) / len(set(ground_truth_ids))

    def _compute_mrr(self, retrieved_ids: Sequence[str], ground_truth_ids: Sequence[str]) -> float:
        """Compute Mean Reciprocal Rank (MRR)."""
        if not ground_truth_ids:
            return 0.0
        for rank, item in enumerate(retrieved_ids, start=1):
            if item in ground_truth_ids:
                return 1.0 / rank
        return 0.0
