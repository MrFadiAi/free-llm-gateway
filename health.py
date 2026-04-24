"""Background health checks for provider availability."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from config import ProviderConfig, OPENAI_COMPATIBLE, SPECIAL_PROVIDERS

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 180  # 3 minutes
DOWN_THRESHOLD = 2  # consecutive failures before marking down
COOLDOWN_SECONDS = 300  # retry down provider after 5 minutes


@dataclass
class ProviderHealth:
    status: str = "unknown"  # up, down, unknown
    last_check_time: float = 0.0
    last_error: str | None = None
    consecutive_failures: int = 0
    latency_ms: float = 0.0


class HealthChecker:
    """Runs periodic health checks on all configured providers."""

    def __init__(self) -> None:
        self._health: dict[str, ProviderHealth] = {}
        self._task: asyncio.Task | None = None

    def get_health(self, provider: str) -> ProviderHealth:
        return self._health.get(provider, ProviderHealth())

    def get_all_health(self) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "status": h.status,
                "last_check_time": h.last_check_time,
                "last_error": h.last_error,
                "consecutive_failures": h.consecutive_failures,
                "latency_ms": round(h.latency_ms, 1),
            }
            for name, h in self._health.items()
        }

    def is_available(self, provider: str) -> bool:
        h = self._health.get(provider)
        if not h or h.status == "unknown":
            return True  # assume available until first check
        if h.status == "up":
            return True
        # status == "down": allow retry after cooldown
        if time.time() - h.last_check_time > COOLDOWN_SECONDS:
            return True
        return False

    async def check_provider(
        self, client: httpx.AsyncClient, provider: ProviderConfig
    ) -> None:
        """Ping a single provider and update its health status."""
        if not provider.api_key:
            self._health[provider.name] = ProviderHealth(status="unknown")
            return

        start = time.time()
        try:
            ok = await self._ping(client, provider)
            latency = (time.time() - start) * 1000

            health = self._health.get(provider.name, ProviderHealth())
            health.last_check_time = time.time()
            health.latency_ms = latency

            if ok:
                health.status = "up"
                health.last_error = None
                health.consecutive_failures = 0
                logger.debug("Health check %s: UP (%.0fms)", provider.name, latency)
            else:
                health.consecutive_failures += 1
                health.last_error = "ping failed"
                if health.consecutive_failures >= DOWN_THRESHOLD:
                    health.status = "down"
                    logger.warning(
                        "Health check %s: DOWN (%d consecutive failures)",
                        provider.name,
                        health.consecutive_failures,
                    )
                else:
                    health.status = "up"  # still up, one failure is ok
                    logger.info(
                        "Health check %s: degraded (failure %d/%d)",
                        provider.name,
                        health.consecutive_failures,
                        DOWN_THRESHOLD,
                    )

            self._health[provider.name] = health

        except Exception as e:
            latency = (time.time() - start) * 1000
            health = self._health.get(provider.name, ProviderHealth())
            health.last_check_time = time.time()
            health.latency_ms = latency
            health.last_error = str(e)[:200]
            health.consecutive_failures += 1
            if health.consecutive_failures >= DOWN_THRESHOLD:
                health.status = "down"
            self._health[provider.name] = health
            logger.debug("Health check %s failed: %s", provider.name, e)

    async def _ping(self, client: httpx.AsyncClient, provider: ProviderConfig) -> bool:
        """Send a lightweight request to check if provider is alive."""
        try:
            if provider.name in OPENAI_COMPATIBLE:
                url = f"{provider.base_url}/models"
                headers = {"Authorization": f"Bearer {provider.api_key}"}
                resp = await client.get(url, headers=headers, timeout=10.0)
                return resp.status_code < 500
            elif provider.name == "cloudflare":
                url = f"{provider.base_url}/models"
                headers = {"Authorization": f"Bearer {provider.api_key}"}
                resp = await client.get(url, headers=headers, timeout=10.0)
                return resp.status_code < 500
            elif provider.name == "google_gemini":
                url = f"{provider.base_url}/models?key={provider.api_key}"
                resp = await client.get(url, timeout=10.0)
                return resp.status_code < 500
            elif provider.name == "huggingface":
                url = f"{provider.base_url}"
                headers = {"Authorization": f"Bearer {provider.api_key}"}
                resp = await client.get(url, headers=headers, timeout=10.0)
                return resp.status_code < 500
            elif provider.name == "cohere":
                url = f"{provider.base_url}/models"
                headers = {"Authorization": f"Bearer {provider.api_key}"}
                resp = await client.get(url, headers=headers, timeout=10.0)
                return resp.status_code < 500
            elif provider.name == "kilo":
                url = f"{provider.base_url}/v1/models"
                headers = {"Authorization": f"Bearer {provider.api_key}"}
                resp = await client.get(url, headers=headers, timeout=10.0)
                return resp.status_code < 500
            else:
                # Generic check
                headers = {"Authorization": f"Bearer {provider.api_key}"}
                resp = await client.get(
                    f"{provider.base_url}/models", headers=headers, timeout=10.0
                )
                return resp.status_code < 500
        except (httpx.TimeoutException, httpx.ConnectError):
            return False

    async def check_all(
        self, client: httpx.AsyncClient, providers: dict[str, ProviderConfig]
    ) -> None:
        """Run health checks on all providers concurrently."""
        tasks = [
            self.check_provider(client, p)
            for p in providers.values()
            if p.api_key
        ]
        if tasks:
            await asyncio.gather(*tasks)

    def start(
        self, client: httpx.AsyncClient, providers: dict[str, ProviderConfig]
    ) -> None:
        """Start the background health check loop."""
        self._task = asyncio.create_task(
            self._loop(client, providers)
        )

    async def stop(self) -> None:
        """Cancel the background task."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(
        self, client: httpx.AsyncClient, providers: dict[str, ProviderConfig]
    ) -> None:
        """Periodically check all providers."""
        while True:
            try:
                await self.check_all(client, providers)
            except Exception as e:
                logger.error("Health check loop error: %s", e)
            await asyncio.sleep(CHECK_INTERVAL)
