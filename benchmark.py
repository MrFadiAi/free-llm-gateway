"""Model benchmarking — tests latency, TTFT, and tokens/sec for each model."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
BENCHMARK_FILE = DATA_DIR / "benchmarks.json"

# Test prompt — simple and fast
TEST_PROMPT = "What is 2+2? Answer with just the number."
TEST_MAX_TOKENS = 10
BENCHMARK_TIMEOUT = 30.0  # seconds per model


class BenchmarkRunner:
    """Runs benchmarks against all configured models."""

    def __init__(self, config) -> None:
        self._config = config
        self._running = False
        self._results: dict[str, Any] = {"results": [], "last_run": None}
        self._load()

    def _load(self) -> None:
        if BENCHMARK_FILE.exists():
            try:
                self._results = json.loads(BENCHMARK_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                logger.warning("Could not load benchmark file")

    def _save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        BENCHMARK_FILE.write_text(json.dumps(self._results, indent=2))

    def is_running(self) -> bool:
        return self._running

    def get_results(self) -> dict:
        return self._results

    async def run_all(self, client: httpx.AsyncClient | None = None) -> dict:
        """Run benchmarks on all models that have at least one available provider."""
        if self._running:
            return self._results

        self._running = True
        own_client = False

        if client is None:
            client = httpx.AsyncClient(http2=True, follow_redirects=True, timeout=BENCHMARK_TIMEOUT)
            own_client = True

        try:
            results = []
            models = list(self._config.models.items())

            # Only benchmark models from models.yaml (manually configured), not auto-discovered
            yaml_models = set()
            try:
                import yaml
                yaml_path = Path(__file__).parent / "models.yaml"
                if yaml_path.exists():
                    yaml_data = yaml.safe_load(yaml_path.read_text())
                    yaml_models = set(yaml_data.get("models", {}).keys())
            except Exception:
                pass

            if yaml_models:
                models = [(k, v) for k, v in models if k in yaml_models]
                logger.info("Benchmarking %d manually configured models (skipping %d auto-discovered)",
                            len(models), len(self._config.models) - len(models))
            else:
                logger.info("Benchmarking %d models...", len(models))

            total = len(models)

            for i, (model_name, model_cfg) in enumerate(models):
                # Get fallbacks
                fallbacks = []
                if hasattr(model_cfg, 'fallbacks'):
                    fallbacks = model_cfg.fallbacks
                elif isinstance(model_cfg, dict):
                    fallbacks = model_cfg.get('fallbacks', [])

                if not fallbacks:
                    continue

                # Try first available provider
                fb = fallbacks[0]
                provider_name = fb.provider if hasattr(fb, 'provider') else fb.get('provider', '')
                provider_model = fb.model if hasattr(fb, 'model') else fb.get('model', '')

                provider = self._config.providers.get(provider_name)
                if not provider or not provider.api_keys:
                    continue

                # Skip if provider base URL not configured
                if not hasattr(provider, 'base_url') or not provider.base_url:
                    continue

                api_key = provider.api_keys[0]
                base_url = str(provider.base_url)

                result = await self._benchmark_one(
                    client, model_name, provider_name, provider_model, base_url, api_key
                )
                results.append(result)

                # Update live results for dashboard
                self._results = {
                    "results": results,
                    "last_run": None,  # Set when complete
                    "total_tested": len(results),
                    "successful": sum(1 for r in results if r.get("success")),
                    "failed": sum(1 for r in results if not r.get("success")),
                    "progress": f"{i+1}/{total}",
                }

                # Log progress
                if (i + 1) % 20 == 0:
                    logger.info("Benchmark progress: %d/%d", i + 1, total)

            self._results = {
                "results": results,
                "last_run": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "total_tested": len(results),
                "successful": sum(1 for r in results if r.get("success")),
                "failed": sum(1 for r in results if not r.get("success")),
            }
            self._save()
            logger.info(
                "Benchmarks complete: %d/%d successful",
                self._results["successful"],
                self._results["total_tested"],
            )
            return self._results

        finally:
            self._running = False
            if own_client:
                await client.aclose()

    async def _benchmark_one(
        self,
        client: httpx.AsyncClient,
        model_name: str,
        provider_name: str,
        provider_model: str,
        base_url: str,
        api_key: str,
    ) -> dict:
        """Benchmark a single model."""
        url = base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": provider_model,
            "messages": [{"role": "user", "content": TEST_PROMPT}],
            "max_tokens": TEST_MAX_TOKENS,
            "stream": True,
        }

        result = {
            "model": model_name,
            "provider": provider_name,
            "provider_model": provider_model,
            "success": False,
            "latency_ms": 0,
            "ttft_ms": 0,
            "tokens_per_second": 0,
            "error": None,
        }

        try:
            start = time.monotonic()
            ttft = None
            token_count = 0

            async with client.stream("POST", url, json=body, headers=headers, timeout=BENCHMARK_TIMEOUT) as resp:
                if resp.status_code >= 400:
                    error_body = await resp.aread()
                    result["error"] = f"HTTP {resp.status_code}: {error_body.decode()[:200]}"
                    result["latency_ms"] = (time.monotonic() - start) * 1000
                    return result

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            if ttft is None:
                                ttft = (time.monotonic() - start) * 1000
                            token_count += 1
                    except json.JSONDecodeError:
                        continue

            end = time.monotonic()
            total_ms = (end - start) * 1000

            result["success"] = True
            result["latency_ms"] = round(total_ms, 1)
            result["ttft_ms"] = round(ttft, 1) if ttft else round(total_ms, 1)
            if token_count > 0 and total_ms > 0:
                result["tokens_per_second"] = round(token_count / (total_ms / 1000), 2)
            else:
                result["tokens_per_second"] = 0

        except httpx.TimeoutException:
            result["error"] = "Timeout"
            result["latency_ms"] = BENCHMARK_TIMEOUT * 1000
        except Exception as e:
            result["error"] = str(e)[:200]
            result["latency_ms"] = (time.monotonic() - start) * 1000

        return result
