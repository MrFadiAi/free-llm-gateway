"""Smart default model selection based on task type and benchmark data."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Keywords that indicate model suitability for each task type
TASK_KEYWORDS: dict[str, list[str]] = {
    "code": ["coder", "codestral", "code", "qwen3-coder", "deepseek-coder"],
    "reasoning": ["r1", "reasoning", "think", "deepseek-r1", "o1", "o3"],
    "vision": [],  # handled via capabilities check
    "fast": [],    # handled via benchmark latency
    "creative": ["hermes", "dolphin", "mistral-large", "llama"],
    "chat": [],    # general purpose, uses tier-based selection
}

# Preferred models per task type (ordered by preference)
TASK_PREFERRED: dict[str, list[str]] = {
    "code": ["qwen3-coder", "deepseek-r1", "gemma-4-31b", "llama-3.3-70b"],
    "reasoning": ["deepseek-r1", "nemotron-super-120b", "qwen3-next-80b"],
    "vision": ["gemma-4-31b", "gemma-4-26b", "gemma-3-27b"],
    "fast": [],  # dynamic from benchmarks
    "creative": ["hermes-3-405b", "mistral-large", "dolphin-mistral-24b", "llama-3.3-70b"],
    "chat": [
        "nemotron-super-120b", "llama-3.3-70b", "gemma-4-31b",
        "mistral-large", "qwen3-next-80b", "gpt-oss-120b",
        "minimax-m2.5", "hermes-3-405b",
    ],
}


class SmartDefault:
    """Picks the best model for a given task type using benchmarks + capabilities."""

    def __init__(self, models: dict[str, Any], benchmark_data: dict[str, Any] | None = None) -> None:
        self._models = models
        self._benchmarks = benchmark_data

    def update_benchmarks(self, benchmark_data: dict[str, Any]) -> None:
        self._benchmarks = benchmark_data

    def get_default(self, task: str) -> dict[str, Any]:
        """Return the recommended model for a task type.

        Returns dict with: model, reason, task, alternatives
        """
        task = task.lower().strip()
        if task not in TASK_KEYWORDS and task not in TASK_PREFERRED:
            task = "chat"

        # Build benchmark lookup
        bench_lookup: dict[str, dict[str, Any]] = {}
        if self._benchmarks:
            for entry in self._benchmarks.get("results", []):
                bench_lookup[entry["model"]] = entry

        # For "fast" task: pick lowest latency from benchmarks
        if task == "fast":
            best = self._pick_fastest(bench_lookup)
            if best:
                return best

        # For "vision": pick models with supports_vision capability
        if task == "vision":
            best = self._pick_vision()
            if best:
                return best

        # For other tasks: keyword matching + preferred list + benchmark scoring
        return self._pick_by_task(task, bench_lookup)

    def _pick_fastest(self, bench_lookup: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        """Pick the fastest model from benchmark data."""
        candidates = [
            (name, entry)
            for name, entry in bench_lookup.items()
            if entry.get("success") and name in self._models
        ]
        if not candidates:
            # Fallback: pick first available known-fast model
            for m in ["llama-3.1-8b", "gemma-3-4b", "gemma-3-12b"]:
                if m in self._models:
                    return {
                        "model": m, "task": "fast",
                        "reason": "Lightweight model (no benchmark data)",
                        "alternatives": [],
                    }
            return None

        candidates.sort(key=lambda x: x[1].get("latency_ms", float("inf")))
        best_name, best_entry = candidates[0]
        alts = [name for name, _ in candidates[1:4]]
        return {
            "model": best_name,
            "task": "fast",
            "reason": f"Lowest latency: {best_entry.get('latency_ms', 0):.0f}ms",
            "alternatives": alts,
            "latency_ms": best_entry.get("latency_ms", 0),
        }

    def _pick_vision(self) -> dict[str, Any] | None:
        """Pick a model with vision support."""
        preferred = TASK_PREFERRED.get("vision", [])
        for m in preferred:
            if m in self._models:
                model_cfg = self._models[m]
                if hasattr(model_cfg, "capabilities") and model_cfg.capabilities.supports_vision:
                    return {
                        "model": m, "task": "vision",
                        "reason": "Supports vision input",
                        "alternatives": [
                            pm for pm in preferred
                            if pm != m and pm in self._models
                        ],
                    }
        # Fallback: scan all models for vision capability
        for m, cfg in self._models.items():
            if hasattr(cfg, "capabilities") and cfg.capabilities.supports_vision:
                return {
                    "model": m, "task": "vision",
                    "reason": "Supports vision input",
                    "alternatives": [],
                }
        return None

    def _pick_by_task(self, task: str, bench_lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Pick a model for a task using keyword matching and preference lists."""
        keywords = TASK_KEYWORDS.get(task, [])
        preferred = TASK_PREFERRED.get(task, [])

        # Score each available model
        scored: list[tuple[str, float, str]] = []
        for model_name in self._models:
            score = 0.0
            reason_parts = []

            # Keyword matching on model name
            name_lower = model_name.lower()
            for kw in keywords:
                if kw in name_lower:
                    score += 10.0
                    reason_parts.append(f"keyword match: {kw}")
                    break

            # Preferred list position
            if model_name in preferred:
                idx = preferred.index(model_name)
                score += max(0, 5.0 - idx)
                reason_parts.append("preferred")

            # Benchmark bonus (lower latency = higher score)
            bench = bench_lookup.get(model_name)
            if bench and bench.get("success"):
                latency = bench.get("latency_ms", 10000)
                score += max(0, 3.0 - (latency / 1000))
                reason_parts.append(f"latency: {latency:.0f}ms")

            if score > 0 or task == "chat":
                scored.append((model_name, score, ", ".join(reason_parts) or "general purpose"))

        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)

        # Fallback to chat preferred if nothing scored
        if not scored:
            for m in TASK_PREFERRED.get("chat", []):
                if m in self._models:
                    return {
                        "model": m, "task": task,
                        "reason": "General purpose fallback",
                        "alternatives": [],
                    }
            # Last resort: first available model
            if self._models:
                m = next(iter(self._models))
                return {
                    "model": m, "task": task,
                    "reason": "Only available model",
                    "alternatives": [],
                }
            return {"model": "", "task": task, "reason": "No models available", "alternatives": []}

        best_name, _, reason = scored[0]
        alts = [name for name, _, _ in scored[1:5]]
        return {
            "model": best_name,
            "task": task,
            "reason": reason,
            "alternatives": alts,
        }

    def get_all_defaults(self) -> dict[str, dict[str, Any]]:
        """Return recommended models for all task types."""
        results = {}
        for task in ["chat", "code", "reasoning", "fast", "creative", "vision"]:
            results[task] = self.get_default(task)
        return results
