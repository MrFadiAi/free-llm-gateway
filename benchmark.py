"""Model benchmarking — measures latency, TTFT, and tokens/sec for each model."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BENCHMARK_FILE = Path("data/benchmarks.json")
BENCHMARK_PROMPT = "What is 2+2? Answer with just the number."
MAX_TOKENS = 10
TIMEOUT = 30.0


@dataclass
class BenchmarkResult:
    model: str
    provider: str
    provider_model: str
    latency_ms: float = 0.0
    ttft_ms: float | None = None
    tokens_per_second: float | None = None
    success: bool = False
    error: str | None = None


class BenchmarkRunner:
    """Runs a quick benchmark on each configured model and stores results."""

    def __init__(self, config: Any) -> None:
        self._config = config
        self._results: list[BenchmarkResult] = []
        self._running = False
        self._load_results()

    # ── Persistence ──────────────────────────────────────────────

    def _load_results(self) -> None:
        if BENCHMARK_FILE.exists():
            try:
                data = json.loads(BENCHMARK_FILE.read_text())
                self._results = [BenchmarkResult(**r) for r in data.get("results", [])]
                logger.info("Loaded %d benchmark results", len(self._results))
            except Exception as e:
                logger.warning("Failed to load benchmarks: %s", e)

    def _save_results(self) -> None:
        BENCHMARK_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {"results": [asdict(r) for r in self._results]}
        BENCHMARK_FILE.write_text(json.dumps(data, indent=2))

    # ── Public API ───────────────────────────────────────────────

    def get_results(self) -> dict[str, Any]:
        return {"results": [asdict(r) for r in self._results]}

    def is_running(self) -> bool:
        return self._running

    async def run_all(self) -> dict[str, Any]:
        """Run benchmarks on all models that have an active provider."""
        if self._running:
            return {"status": "already_running", "results": []}

        self._running = True
        results: list[BenchmarkResult] = []

        try:
            # Build list of (model_name, provider_name, provider_model, provider_config)
            tasks = []
            for model_name, model_cfg in self._config.models.items():
                if not hasattr(model_cfg, "fallbacks") or not model_cfg.fallbacks:
                    continue
                # Use first available provider
                for fb in model_cfg.fallbacks:
                    prov_name = fb.provider
                    prov_model = fb.model
                    prov_cfg = self._config.providers.get(prov_name)
                    if prov_cfg and prov_cfg.api_keys:
                        tasks.append((model_name, prov_name, prov_model, prov_cfg))
                        break  # one provider per model is enough

            logger.info("Starting benchmarks for %d models", len(tasks))

            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                for model_name, prov_name, prov_model, prov_cfg in tasks:
                    result = await self._benchmark_one(
                        client, model_name, prov_name, prov_model, prov_cfg
                    )
                    results.append(result)

            self._results = results
            self._save_results()
            logger.info("Benchmarks complete: %d models tested", len(results))

        finally:
            self._running = False

        return {"results": [asdict(r) for r in results]}

    async def _benchmark_one(
        self,
        client: httpx.AsyncClient,
        model_name: str,
        provider_name: str,
        provider_model: str,
        prov_cfg: Any,
    ) -> BenchmarkResult:
        """Run a single streaming benchmark request."""
        result = BenchmarkResult(
            model=model_name,
            provider=provider_name,
            provider_model=provider_model,
        )

        try:
            url = prov_cfg.base_url.rstrip("/") + "/chat/completions"
            headers = {
                "Authorization": f"Bearer {prov_cfg.api_keys[0]}",
                "Content-Type": "application/json",
            }
            body = {
                "model": provider_model,
                "messages": [{"role": "user", "content": BENCHMARK_PROMPT}],
                "max_tokens": MAX_TOKENS,
                "stream": True,
            }

            start = time.monotonic()
            ttft: float | None = None
            token_count = 0

            async with client.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code != 200:
                    error_body = await resp.aread()
                    result.error = f"HTTP {resp.status_code}: {error_body.decode()[:200]}"
                    result.success = False
                    return result

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    if ttft is None:
                        ttft = (time.monotonic() - start) * 1000
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            token_count += 1
                    except json.JSONDecodeError:
                        pass

            elapsed_ms = (time.monotonic() - start) * 1000
            result.latency_ms = elapsed_ms
            result.ttft_ms = ttft
            result.tokens_per_second = (token_count / (elapsed_ms / 1000)) if elapsed_ms > 0 and token_count > 0 else None
            result.success = True

        except Exception as e:
            result.error = str(e)[:200]
            result.success = False

        return result
