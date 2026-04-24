"""Async request queue — holds requests when all providers are rate-limited."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MAX_QUEUE_SIZE = int(os.environ.get("QUEUE_MAX_SIZE", "100"))
MAX_WAIT_TIME = int(os.environ.get("QUEUE_MAX_WAIT", "120"))
BLOCKING_QUEUE = os.environ.get("BLOCKING_QUEUE", "true").lower() in ("true", "1", "yes")

# Exponential backoff delays in seconds
BACKOFF_DELAYS = [5, 15, 30, 60]


@dataclass
class QueuedRequest:
    id: str
    model: str
    payload: dict[str, Any]
    enqueued_at: float
    result: dict[str, Any] | None = None
    error: str | None = None
    status: str = "queued"  # queued | processing | completed | failed
    attempts: int = 0
    max_attempts: int = len(BACKOFF_DELAYS)
    completed_event: asyncio.Event = field(default_factory=asyncio.Event)


class RequestQueue:
    """Manages queued requests that failed due to provider rate limits."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[QueuedRequest] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._pending: dict[str, QueuedRequest] = {}
        self._workers: list[asyncio.Task] = []
        self._total_queued = 0
        self._total_completed = 0
        self._total_failed = 0
        self._total_expired = 0
        self._router = None  # set during startup
        self._running = False

    def set_router(self, router: Any) -> None:
        self._router = router

    async def start_workers(self, num_workers: int = 3) -> None:
        """Start background worker tasks."""
        self._running = True
        for i in range(num_workers):
            task = asyncio.create_task(self._worker(i))
            self._workers.append(task)
        logger.info("Request queue started with %d workers", num_workers)

    async def stop_workers(self) -> None:
        """Stop all worker tasks gracefully."""
        self._running = False
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def enqueue(
        self, model: str, payload: dict[str, Any]
    ) -> tuple[str, float, QueuedRequest | None]:
        """Add a request to the queue.

        Returns (request_id, estimated_wait_seconds, queued_request).
        If the queue is full, raises RuntimeError.
        """
        req = QueuedRequest(
            id=uuid.uuid4().hex[:12],
            model=model,
            payload=payload,
            enqueued_at=time.time(),
        )

        try:
            self._queue.put_nowait(req)
        except asyncio.QueueFull:
            raise RuntimeError("Request queue is full. Try again later.")

        self._pending[req.id] = req
        self._total_queued += 1

        # Rough estimate: queue depth * average backoff
        depth = self._queue.qsize()
        estimated_wait = min(depth * 10, MAX_WAIT_TIME)

        logger.info("Queued request %s for model %s (depth=%d)", req.id, model, depth)
        return req.id, estimated_wait, req

    async def get_result(self, request_id: str, timeout: float = MAX_WAIT_TIME) -> dict[str, Any]:
        """Wait for a queued request to complete. Returns the result or raises."""
        req = self._pending.get(request_id)
        if not req:
            raise ValueError(f"Unknown request: {request_id}")

        try:
            await asyncio.wait_for(req.completed_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            req.status = "failed"
            req.error = "Request timed out waiting in queue"
            self._total_expired += 1
            raise

        if req.error:
            raise RuntimeError(req.error)
        return req.result

    async def _worker(self, worker_id: int) -> None:
        """Worker loop that processes queued requests with backoff."""
        while self._running:
            try:
                req = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            await self._process_request(req, worker_id)

    async def _process_request(self, req: QueuedRequest, worker_id: int) -> None:
        """Process a single queued request with exponential backoff retries."""
        req.status = "processing"

        for attempt in range(req.max_attempts):
            delay = BACKOFF_DELAYS[min(attempt, len(BACKOFF_DELAYS) - 1)]
            if attempt > 0:
                logger.info(
                    "Worker %d retrying %s (attempt %d, waiting %ds)",
                    worker_id, req.id, attempt + 1, delay,
                )
                await asyncio.sleep(delay)

            # Check if request expired
            if time.time() - req.enqueued_at > MAX_WAIT_TIME:
                req.status = "failed"
                req.error = "Request expired in queue"
                self._total_expired += 1
                req.completed_event.set()
                return

            req.attempts = attempt + 1
            try:
                if not self._router:
                    raise RuntimeError("Router not configured")

                import httpx
                async with httpx.AsyncClient(http2=True, follow_redirects=True) as client:
                    result, provider, provider_model = await self._router.route_request(
                        req.model, req.payload, client
                    )

                if isinstance(result, dict):
                    req.result = result
                    req.status = "completed"
                    self._total_completed += 1
                    req.completed_event.set()
                    logger.info("Worker %d completed queued request %s", worker_id, req.id)
                    return
                else:
                    # Streaming result — can't cache/return from queue
                    req.status = "failed"
                    req.error = "Cannot queue streaming requests"
                    self._total_failed += 1
                    req.completed_event.set()
                    return

            except Exception as e:
                logger.warning(
                    "Worker %d attempt %d failed for %s: %s",
                    worker_id, attempt + 1, req.id, str(e)[:200],
                )
                continue

        # All retries exhausted
        req.status = "failed"
        req.error = f"All {req.max_attempts} retry attempts failed"
        self._total_failed += 1
        req.completed_event.set()

    def stats(self) -> dict[str, Any]:
        return {
            "queue_depth": self._queue.qsize(),
            "max_size": MAX_QUEUE_SIZE,
            "pending_requests": len(self._pending),
            "total_queued": self._total_queued,
            "total_completed": self._total_completed,
            "total_failed": self._total_failed,
            "total_expired": self._total_expired,
            "workers": len(self._workers),
            "blocking_mode": BLOCKING_QUEUE,
            "max_wait_seconds": MAX_WAIT_TIME,
        }

    def get_pending_list(self) -> list[dict[str, Any]]:
        now = time.time()
        results = []
        for req in self._pending.values():
            results.append({
                "id": req.id,
                "model": req.model,
                "status": req.status,
                "enqueued_at": req.enqueued_at,
                "wait_seconds": round(now - req.enqueued_at, 1),
                "attempts": req.attempts,
            })
        return results


# Global singleton
request_queue = RequestQueue()
