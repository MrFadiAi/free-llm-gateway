"""Free LLM Gateway — unified OpenAI-compatible API server."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cache import ResponseCache, response_cache
import sys
from pathlib import Path

from smart_router import SmartRouter
from config import load_config, AppConfig, discover_models, PROVIDER_DEFS
from health import HealthChecker
from key_manager import KeyManager
from providers import has_tool_calling
from request_queue import RequestQueue, request_queue, BLOCKING_QUEUE
from rate_limiter import RateLimiter
from router import Router, AllRateLimitedError
from benchmark import BenchmarkRunner
from smart_default import SmartDefault
from smart_router import SmartRouter
from tracking import UsageTracker, usage_tracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ── Globals ──────────────────────────────────────────────────────────────────
config: AppConfig = load_config()
rate_limiter = RateLimiter()
health_checker = HealthChecker()
router = Router(config, rate_limiter, health_checker)
templates = Jinja2Templates(directory="templates")
key_manager = KeyManager(config.master_key or "default-key")
smart_router = SmartRouter(config.models)
benchmark_runner = BenchmarkRunner(config)
# Smart router for model aliases
smart_default = SmartDefault(config.models, benchmark_runner.get_results())

# Shared httpx client (reused across requests for connection pooling)
_client: httpx.AsyncClient | None = None


def _sync_keys_to_config() -> None:
    """Push key_manager keys into config.providers so the router picks them up."""
    for name, prov in config.providers.items():
        km_keys = key_manager.get_keys(name)
        if km_keys:
            prov.api_keys = km_keys


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client
    _client = httpx.AsyncClient(http2=True, follow_redirects=True)

    # Auto-discover models from providers
    try:
        discovered = await discover_models(_client, config)
        if discovered:
            logger.info(
                "Discovered %d models: %s",
                len(discovered),
                ", ".join(list(discovered.keys())[:10]),
            )
    except Exception as e:
        logger.warning("Model discovery failed: %s", e)

    # Start background health checks
    health_checker.start(_client, config.providers)
    # Run an initial health check immediately
    await health_checker.check_all(_client, config.providers)

    # Sync key_manager keys into config providers
    _sync_keys_to_config()

    # Startup complete

    # Start request queue workers
    request_queue.set_router(router)
    await request_queue.start_workers(num_workers=3)

    logger.info(
        "Gateway started — %d models, %d providers configured",
        len(config.models),
        sum(1 for p in config.providers.values() if p.api_key),
    )
    yield

    # Shutdown
    await request_queue.stop_workers()
    usage_tracker.flush()
    await health_checker.stop()
    if _client:
        await _client.aclose()


app = FastAPI(title="Free LLM Gateway", version="1.0.0", lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Auth middleware ───────────────────────────────────────────────────────────
def verify_master_key(authorization: str | None) -> None:
    if not config.master_key:
        return  # no auth if MASTER_KEY not set
    if not authorization:
        raise HTTPException(401, "Missing Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    if token != config.master_key:
        raise HTTPException(401, "Invalid API key")


# ── Chat completions ─────────────────────────────────────────────────────────
@app.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: str | None = Header(None)):
    verify_master_key(authorization)
    body = await request.json()
    model = body.get("model", "")
    stream = body.get("stream", False)

    if not model:
        raise HTTPException(400, "Missing 'model' field")

    # ── Smart routing: resolve model name via aliases and equivalence ──
    resolved = smart_router.resolve(model)
    if resolved.substitution:
        logger.info("Smart routing: %s", resolved.substitution)
    model = resolved.resolved_name

    # ── Tool calling auto-routing ──
    if has_tool_calling(body):
        model_cfg = config.models.get(model)
        if model_cfg and not model_cfg.capabilities.supports_tools:
            alt = smart_router.find_model_with_capability("supports_tools")
            if alt:
                logger.info(
                    "Auto-routing: '%s' doesn't support tools -> '%s'",
                    model, alt,
                )
                model = alt
            else:
                raise HTTPException(
                    400,
                    f"Model '{model}' does not support tool calling and no alternative found",
                )

    # ── Cache check (non-streaming only) ──
    cache_headers = {"X-Cache": "MISS"}
    if not stream:
        cache_key = ResponseCache.make_key(
            model, body.get("messages", []), body.get("temperature")
        )
        cached, hit = response_cache.get(cache_key)
        if hit:
            cache_headers["X-Cache"] = "HIT"
            cached.setdefault("model", model)
            return JSONResponse(content=cached, headers=cache_headers)

    # ── Route request ──
    try:
        result, provider, provider_model = await router.route_request(model, body, _client)
    except AllRateLimitedError:
        # All providers rate-limited → queue the request
        return await _handle_rate_limited(model, body, stream)

    # ── Record token usage ──
    if isinstance(result, dict):
        usage = result.get("usage")
        if usage and isinstance(usage, dict):
            usage_tracker.record(
                model=model,
                provider=provider,
                prompt_tokens=usage.get("prompt_tokens", 0) or 0,
                completion_tokens=usage.get("completion_tokens", 0) or 0,
                total_tokens=usage.get("total_tokens", 0) or 0,
            )

        # ── Cache the response (non-streaming only) ──
        if not stream:
            response_cache.put(cache_key, result)

    # ── Return response ──
    if stream and hasattr(result, "__aiter__"):
        return StreamingResponse(
            result,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Provider": provider,
                "X-Provider-Model": provider_model,
                "X-Cache": "BYPASS",
            },
        )

    # Non-streaming: ensure it's a dict
    if isinstance(result, dict):
        result.setdefault("model", provider_model)
    cache_headers["X-Provider"] = provider
    cache_headers["X-Provider-Model"] = provider_model
    return JSONResponse(content=result, headers=cache_headers)


async def _handle_rate_limited(model: str, body: dict, stream: bool):
    """Handle the case where all providers are rate-limited."""
    if stream:
        raise HTTPException(429, "All providers rate-limited. Retry later.")

    try:
        req_id, wait_time, queued_req = await request_queue.enqueue(model, body)
    except RuntimeError:
        raise HTTPException(503, "Queue is full. All providers rate-limited. Retry later.")

    if BLOCKING_QUEUE and queued_req:
        # Block and wait for result
        try:
            result = await request_queue.get_result(req_id)
            return JSONResponse(
                content=result,
                headers={"X-Queued": "true", "X-Request-Id": req_id},
            )
        except Exception:
            raise HTTPException(504, "Queued request timed out")

    # Non-blocking: return 202 with polling info
    return JSONResponse(
        status_code=202,
        content={
            "id": req_id,
            "status": "queued",
            "estimated_wait_seconds": wait_time,
            "poll_url": f"/api/queue/{req_id}",
        },
        headers={"X-Request-Id": req_id},
    )


# ── Models list ──────────────────────────────────────────────────────────────
@app.get("/v1/models")
async def list_models(authorization: str | None = Header(None)):
    verify_master_key(authorization)
    models = []
    for name, model_cfg in config.models.items():
        providers = [
            {"provider": fb.provider, "model": fb.model}
            for fb in model_cfg.fallbacks
        ]
        models.append({
            "id": name,
            "object": "model",
            "owned_by": "free-llm-gateway",
            "providers": providers,
            "capabilities": {
                "supports_tools": model_cfg.capabilities.supports_tools,
                "supports_vision": model_cfg.capabilities.supports_vision,
                "supports_streaming": model_cfg.capabilities.supports_streaming,
            },
        })
    return {"object": "list", "data": models}


# ── Embeddings (pass-through) ────────────────────────────────────────────────
@app.post("/v1/embeddings")
async def embeddings(request: Request, authorization: str | None = Header(None)):
    verify_master_key(authorization)
    body = await request.json()
    model = body.get("model", "")

    fallbacks = router.get_fallbacks(model)
    if not fallbacks:
        raise HTTPException(400, f"Unknown model: {model}")

    for fb in fallbacks:
        provider = config.providers.get(fb.provider)
        if not provider or not provider.api_key:
            continue
        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{provider.base_url}/embeddings"
        body["model"] = fb.model
        try:
            resp = await _client.post(url, headers=headers, json=body, timeout=60.0)
            if resp.status_code < 400:
                return resp.json()
        except Exception as e:
            logger.warning("Embedding provider %s failed: %s", fb.provider, e)
            continue

    raise HTTPException(502, "All embedding providers failed")


# ── Usage tracking API ───────────────────────────────────────────────────────
@app.get("/api/usage")
async def api_usage(authorization: str | None = Header(None)):
    verify_master_key(authorization)
    return usage_tracker.get_stats()


# ── Cache API ────────────────────────────────────────────────────────────────
@app.get("/api/cache")
async def api_cache_stats(authorization: str | None = Header(None)):
    verify_master_key(authorization)
    return response_cache.stats()


@app.delete("/api/cache")
async def api_cache_clear(authorization: str | None = Header(None)):
    verify_master_key(authorization)
    cleared = response_cache.clear()
    return {"cleared": cleared}


@app.post("/api/cache/prune")
async def api_cache_prune(authorization: str | None = Header(None)):
    verify_master_key(authorization)
    pruned = response_cache.prune_expired()
    return {"pruned": pruned}


# ── Queue API ────────────────────────────────────────────────────────────────
@app.get("/api/queue")
async def api_queue_stats(authorization: str | None = Header(None)):
    verify_master_key(authorization)
    return {
        **request_queue.stats(),
        "pending": request_queue.get_pending_list(),
    }


@app.get("/api/queue/{request_id}")
async def api_queue_poll(request_id: str, authorization: str | None = Header(None)):
    verify_master_key(authorization)
    req = request_queue._pending.get(request_id)
    if not req:
        raise HTTPException(404, f"Request {request_id} not found")
    return {
        "id": req.id,
        "model": req.model,
        "status": req.status,
        "attempts": req.attempts,
        "result": req.result,
        "error": req.error,
    }


# ── Dashboard ────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    from pathlib import Path
    html = Path("templates/dashboard.html").read_text()
    return HTMLResponse(content=html)


# ── Model resolution ─────────────────────────────────────────────────────────
@app.get("/api/models/resolve")
async def resolve_model(name: str, authorization: str | None = Header(None)):
    """Resolve a model name through aliases and equivalence mapping."""
    verify_master_key(authorization)
    if not name:
        raise HTTPException(400, "Missing 'name' query parameter")
    result = smart_router.resolve(name)
    model_cfg = config.models.get(result.resolved_name)
    capabilities = None
    if model_cfg:
        capabilities = {
            "supports_tools": model_cfg.capabilities.supports_tools,
            "supports_vision": model_cfg.capabilities.supports_vision,
            "supports_streaming": model_cfg.capabilities.supports_streaming,
        }
    return {
        "original_name": result.original_name,
        "resolved_name": result.resolved_name,
        "alias_used": result.alias_used,
        "substitution": result.substitution,
        "available": result.resolved_name in config.models,
        "capabilities": capabilities,
    }


@app.get("/api/status")
async def api_status():
    providers_info = {}
    for name, p in config.providers.items():
        providers_info[name] = {
            "has_key": bool(p.api_keys),
            "total_keys": p.total_keys,
            "active_key_index": p.active_key_index,
            "base_url": p.base_url,
        }
    return {
        "models": router.get_model_status(),
        "rate_limits": rate_limiter.get_all_status(),
        "health": health_checker.get_all_health(),
        "logs": router.get_logs(50),
        "usage": usage_tracker.get_stats(),
        "cache": response_cache.stats(),
        "queue": request_queue.stats(),
        "providers": providers_info,
    }


# ── Model discovery ──────────────────────────────────────────────────────────
@app.post("/api/discover")
async def api_discover(authorization: str | None = Header(None)):
    verify_master_key(authorization)
    if not _client:
        raise HTTPException(503, "Server not ready")
    discovered = await discover_models(_client, config)
    return {
        "discovered_count": len(discovered),
        "discovered_models": list(discovered.keys()),
        "total_models": len(config.models),
    }


# ── API Key Management ───────────────────────────────────────────────────────
@app.post("/api/keys")
async def add_key(request: Request):
    body = await request.json()
    provider = body.get("provider", "").strip()
    key = body.get("key", "").strip()
    if not provider or not key:
        raise HTTPException(400, "Missing 'provider' or 'key'")
    if provider not in PROVIDER_DEFS:
        raise HTTPException(400, f"Unknown provider: {provider}")

    # Validate key against provider's /models endpoint
    valid = False
    validation_error = None
    prov_def = PROVIDER_DEFS[provider]
    base_url = prov_def[1]
    if "{account_id}" in base_url:
        base_url = base_url.replace(
            "{account_id}", os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        )
    test_url = f"{base_url}/models"
    headers = {"Authorization": f"Bearer {key}"}
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/free-llm-gateway"
        headers["X-Title"] = "Free LLM Gateway"
    try:
        if _client:
            resp = await _client.get(test_url, headers=headers, timeout=15.0)
            valid = resp.status_code < 400
            if not valid:
                validation_error = f"HTTP {resp.status_code}"
    except Exception as e:
        validation_error = str(e)[:200]

    index = key_manager.add_key(provider, key)
    key_manager.set_validated(provider, index, valid)
    _sync_keys_to_config()
    return {
        "ok": True, "provider": provider, "index": index,
        "valid": valid, "validation_error": validation_error,
    }


@app.delete("/api/keys/{provider}/{index}")
async def remove_key(provider: str, index: int):
    if not key_manager.remove_key(provider, index):
        raise HTTPException(404, "Key not found")
    _sync_keys_to_config()
    return {"ok": True}


@app.get("/api/keys")
async def list_keys():
    """List all keys: .env-sourced (read-only) + runtime-added (deletable)."""
    runtime_keys = key_manager.list_keys()
    # Build .env keys from config.providers
    env_keys: dict[str, list[dict[str, Any]]] = {}
    for name, prov in config.providers.items():
        if prov.api_keys:
            env_keys[name] = []
            for i, key in enumerate(prov.api_keys):
                masked = "****" + key[-4:] if len(key) > 4 else "****"
                # Check if this key also exists in runtime keys
                runtime_entries = runtime_keys.get(name, [])
                is_runtime = any(
                    e.get("key_masked") == masked for e in runtime_entries
                )
                if not is_runtime:
                    env_keys[name].append({
                        "index": i,
                        "key_masked": masked,
                        "source": "env",
                        "validated": None,
                        "deletable": False,
                        "added_at": "",
                    })

    # Mark runtime keys with source
    all_keys: dict[str, list[dict[str, Any]]] = {}
    all_providers = set(list(runtime_keys.keys()) + list(env_keys.keys()))
    for pname in all_providers:
        entries = []
        # .env keys first
        for e in env_keys.get(pname, []):
            entries.append(e)
        # runtime keys
        for e in runtime_keys.get(pname, []):
            entry = {**e, "source": "runtime", "deletable": True}
            entries.append(entry)
        if entries:
            all_keys[pname] = entries
    return {"keys": all_keys}


@app.post("/api/keys/{provider}/validate")
async def validate_provider_keys(provider: str):
    """Validate all keys for a provider by testing each against the /models endpoint."""
    keys = key_manager.get_keys(provider)
    if not keys:
        raise HTTPException(404, f"No keys found for provider: {provider}")

    prov_def = PROVIDER_DEFS.get(provider)
    if not prov_def:
        raise HTTPException(400, f"Unknown provider: {provider}")

    results = []
    for i, api_key in enumerate(keys):
        base_url = prov_def[1]
        if "{account_id}" in base_url:
            base_url = base_url.replace("{account_id}", os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""))
        test_url = f"{base_url}/models"
        headers = {"Authorization": f"Bearer " + api_key}
        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/free-llm-gateway"
            headers["X-Title"] = "Free LLM Gateway"
        try:
            resp = await _client.get(test_url, headers=headers, timeout=15.0)
            valid = resp.status_code < 400
            key_manager.set_validated(provider, i, valid)
            results.append({"index": i, "valid": valid})
        except Exception as e:
            key_manager.set_validated(provider, i, False)
            results.append({"index": i, "valid": False, "error": str(e)[:200]})

    return {"provider": provider, "results": results}


@app.post("/api/keys/{provider}/{index}/validate")
async def validate_key(provider: str, index: int):
    keys = key_manager.get_keys(provider)
    if index < 0 or index >= len(keys):
        raise HTTPException(404, "Key not found")

    api_key = keys[index]
    prov_def = PROVIDER_DEFS.get(provider)
    if not prov_def:
        raise HTTPException(400, f"Unknown provider: {provider}")

    base_url = prov_def[1]
    if "{account_id}" in base_url:
        import os
        base_url = base_url.replace("{account_id}", os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""))

    # Try a lightweight models list request
    test_url = f"{base_url}/models"
    headers = {"Authorization": f"Bearer " + api_key}
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/free-llm-gateway"
        headers["X-Title"] = "Free LLM Gateway"

    try:
        resp = await _client.get(test_url, headers=headers, timeout=15.0)
        valid = resp.status_code < 400
        key_manager.set_validated(provider, index, valid)
        if valid:
            return {"valid": True}
        else:
            return {"valid": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        key_manager.set_validated(provider, index, False)
        return {"valid": False, "error": str(e)[:200]}


# ── Connection info & Config export ──────────────────────────────────────────
TOP_RECOMMENDED_MODELS = [
    "nemotron-super-120b", "llama-3.3-70b", "deepseek-r1",
    "gemma-4-31b", "qwen3-coder", "mistral-large",
    "gpt-oss-120b", "hermes-3-405b", "minimax-m2.5", "qwen3-next-80b",
]


def _get_base_url() -> str:
    host = config.host if config.host != "0.0.0.0" else "localhost"
    return f"http://{host}:{config.port}/v1"


@app.post("/api/sync-providers")
async def api_sync_providers(authorization: str | None = Header(None)):
    """Sync providers from awesome-free-llm-apis upstream."""
    verify_master_key(authorization)
    import subprocess
    import json as _json
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "sync_providers.py")],
            capture_output=True, text=True, timeout=60,
        )
        # Reload config after sync
        global config
        config = load_config()
        # Count models
        total = len(config.models)
        return {
            "ok": True,
            "output": result.stdout[-500:] if result.stdout else "",
            "new_models": 0,  # sync_providers.py handles counting
            "providers": len(config.provider_keys),
            "total_models": total,
        }
    except Exception as e:
        raise HTTPException(500, f"Sync failed: {e}")


@app.get("/api/auto-update")
async def api_auto_update(authorization: str | None = Header(None)):
    """Trigger auto-update: re-discover models from all providers."""
    verify_master_key(authorization)
    if not _client:
        raise HTTPException(503, "Server not ready")

    old_count = len(config.models)
    discovered = await discover_models(_client, config)
    new_count = len(config.models)
    added = new_count - old_count

    return {
        "ok": True,
        "previous_model_count": old_count,
        "current_model_count": new_count,
        "new_models_discovered": added,
        "discovered_models": list(discovered.keys())[:20] if discovered else [],
    }


@app.get("/api/connection-info")
async def api_connection_info():
    base_url = _get_base_url()
    master_key = config.master_key or ""
    masked = ""
    if master_key:
        masked = ("*" * max(0, len(master_key) - 4)) + master_key[-4:]
    else:
        masked = "(not set)"
    available_top = [m for m in TOP_RECOMMENDED_MODELS if m in config.models][:10]
    return {
        "base_url": base_url,
        "master_key": master_key,
        "master_key_masked": masked,
        "model_count": len(config.models),
        "provider_count": sum(1 for p in config.providers.values() if p.api_keys),
        "top_models": available_top,
    }


@app.get("/api/config/openclaw")
async def config_openclaw(authorization: str | None = Header(None)):
    verify_master_key(authorization)
    base_url = _get_base_url()
    available_top = [m for m in TOP_RECOMMENDED_MODELS if m in config.models][:10]
    return {
        "api_key": config.master_key or "",
        "base_url": base_url,
        "default_model": available_top[0] if available_top else "",
        "models": available_top,
    }


@app.get("/api/config/hermes")
async def config_hermes(authorization: str | None = Header(None)):
    verify_master_key(authorization)
    base_url = _get_base_url()
    available_top = [m for m in TOP_RECOMMENDED_MODELS if m in config.models][:10]
    return {
        "openai_api_key": config.master_key or "",
        "openai_base_url": base_url,
        "model": available_top[0] if available_top else "",
        "available_models": available_top,
    }


@app.get("/api/config/env")
async def config_env(authorization: str | None = Header(None)):
    verify_master_key(authorization)
    base_url = _get_base_url()
    lines = [
        f"OPENAI_API_KEY={config.master_key or ''}",
        f"OPENAI_BASE_URL={base_url}",
        "DEFAULT_MODEL=nemotron-super-120b",
    ]
    return {"env_string": "\n".join(lines)}


# ── Batch requests ────────────────────────────────────────────────────────────
BATCH_MAX_SIZE = int(os.environ.get("BATCH_MAX_SIZE", "10"))


# ── Smart Default API ────────────────────────────────────────────────────────
@app.get("/api/smart-default")
async def api_smart_default(task: str = "chat", authorization: str | None = Header(None)):
    verify_master_key(authorization)
    return smart_default.get_default(task)


@app.get("/api/smart-default/all")
async def api_smart_default_all(authorization: str | None = Header(None)):
    verify_master_key(authorization)
    return smart_default.get_all_defaults()


# ── Benchmark API ────────────────────────────────────────────────────────────
@app.get("/api/benchmarks")
async def api_benchmarks():
    return benchmark_runner.get_results()


@app.get("/api/benchmarks/run")
async def api_benchmarks_run():
    if benchmark_runner.is_running():
        return {"status": "already_running"}
    results = await benchmark_runner.run_all()
    smart_default.update_benchmarks(results)
    return results


# ── Analytics API ────────────────────────────────────────────────────────────
@app.get("/api/analytics")
async def api_analytics(authorization: str | None = Header(None)):
    verify_master_key(authorization)
    stats = usage_tracker.get_stats()
    daily = stats.get("today", {})
    week = stats.get("week", {})
    all_time = stats.get("all_time", {})

    # Aggregate model and provider stats from all daily data
    daily_data = usage_tracker._data.get("daily", {})
    model_totals: dict[str, dict[str, int]] = {}
    provider_totals: dict[str, dict[str, int]] = {}
    provider_success_map: dict[str, dict[str, int]] = {}

    for day_key, day_data in daily_data.items():
        for model, mdata in day_data.get("by_model", {}).items():
            if model not in model_totals:
                model_totals[model] = {"requests": 0, "total_tokens": 0}
            model_totals[model]["requests"] += mdata.get("requests", 0)
            model_totals[model]["total_tokens"] += mdata.get("total_tokens", 0)

        for provider, pdata in day_data.get("by_provider", {}).items():
            if provider not in provider_totals:
                provider_totals[provider] = {"requests": 0, "total_tokens": 0}
            provider_totals[provider]["requests"] += pdata.get("requests", 0)
            provider_totals[provider]["total_tokens"] += pdata.get("total_tokens", 0)

    # Success rates from router logs
    logs = router.get_logs(200)
    for log_entry in logs:
        p = log_entry.get("provider", "unknown")
        if p not in provider_success_map:
            provider_success_map[p] = {"success": 0, "total": 0}
        provider_success_map[p]["total"] += 1
        if log_entry.get("success"):
            provider_success_map[p]["success"] += 1

    # Average latency per model from logs
    model_latency: dict[str, list[float]] = {}
    for log_entry in logs:
        m = log_entry.get("model", "")
        if m not in model_latency:
            model_latency[m] = []
        model_latency[m].append(log_entry.get("latency_ms", 0))
    avg_latency = {
        m: round(sum(lats) / len(lats), 1)
        for m, lats in model_latency.items() if lats
    }

    top_models = sorted(model_totals.items(), key=lambda x: x[1]["requests"], reverse=True)[:10]
    top_providers = sorted(provider_totals.items(), key=lambda x: x[1]["requests"], reverse=True)

    # GPT-4 pricing for estimated savings
    gpt4_input = 0.03 / 1000
    gpt4_output = 0.06 / 1000

    def calc_savings(data: dict) -> float:
        inp = data.get("prompt_tokens", 0)
        out = data.get("completion_tokens", 0)
        return round(inp * gpt4_input + out * gpt4_output, 4)

    return {
        "summary": {
            "total_requests": all_time.get("requests", 0),
            "total_tokens": all_time.get("total_tokens", 0),
            "total_prompt_tokens": all_time.get("prompt_tokens", 0),
            "total_completion_tokens": all_time.get("completion_tokens", 0),
            "today_requests": daily.get("requests", 0),
            "today_tokens": daily.get("total_tokens", 0),
            "week_requests": week.get("requests", 0),
            "week_tokens": week.get("total_tokens", 0),
        },
        "savings": {
            "today_usd": calc_savings(daily),
            "week_usd": calc_savings(week),
            "all_time_usd": calc_savings(all_time),
        },
        "top_models": [
            {"model": m, "requests": d["requests"], "tokens": d["total_tokens"]}
            for m, d in top_models
        ],
        "providers": [
            {
                "provider": p,
                "requests": d["requests"],
                "tokens": d["total_tokens"],
                "success_rate": round(
                    provider_success_map.get(p, {}).get("success", 0) /
                    max(provider_success_map.get(p, {}).get("total", 1), 1) * 100, 1
                ),
            }
            for p, d in top_providers
        ],
        "avg_latency_per_model": avg_latency,
        "daily_history": [
            {
                "date": dk,
                "requests": dd.get("requests", 0),
                "tokens": dd.get("total_tokens", 0),
            }
            for dk, dd in sorted(daily_data.items(), reverse=True)[:30]
        ],
    }


# ── Key Health API ───────────────────────────────────────────────────────────
@app.post("/api/keys/validate-all")
async def api_validate_all_keys(authorization: str | None = Header(None)):
    verify_master_key(authorization)
    if not _client:
        raise HTTPException(503, "Server not ready")

    results = {}
    for name, prov_def in PROVIDER_DEFS.items():
        keys = key_manager.get_keys(name)
        # Also include .env keys
        provider = config.providers.get(name)
        if not keys and provider and provider.api_keys:
            keys = provider.api_keys

        if not keys:
            results[name] = {"status": "no_key", "keys": []}
            continue

        base_url = prov_def[1]
        if "{account_id}" in base_url:
            base_url = base_url.replace(
                "{account_id}", os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
            )

        key_results = []
        for i, api_key in enumerate(keys):
            test_url = f"{base_url}/models"
            headers = {"Authorization": f"Bearer {api_key}"}
            if name == "openrouter":
                headers["HTTP-Referer"] = "https://github.com/free-llm-gateway"
                headers["X-Title"] = "Free LLM Gateway"
            try:
                resp = await _client.get(test_url, headers=headers, timeout=15.0)
                if resp.status_code < 400:
                    status = "valid"
                elif resp.status_code == 429:
                    status = "rate_limited"
                elif resp.status_code in (401, 403):
                    status = "invalid"
                else:
                    status = "error"
                key_results.append({"index": i, "status": status})
            except Exception:
                key_results.append({"index": i, "status": "error"})

        overall = "valid"
        if all(k["status"] in ("invalid", "error") for k in key_results):
            overall = "invalid"
        elif any(k["status"] == "rate_limited" for k in key_results):
            overall = "rate_limited"
        elif any(k["status"] == "error" for k in key_results):
            overall = "error"

        results[name] = {"status": overall, "keys": key_results}

    # Summary counts
    valid = sum(1 for r in results.values() if r["status"] == "valid")
    total_with_keys = sum(1 for r in results.values() if r["status"] != "no_key")
    return {
        "summary": {
            "total": total_with_keys,
            "valid": valid,
            "invalid": sum(1 for r in results.values() if r["status"] == "invalid"),
            "rate_limited": sum(1 for r in results.values() if r["status"] == "rate_limited"),
            "no_key": sum(1 for r in results.values() if r["status"] == "no_key"),
            "error": sum(1 for r in results.values() if r["status"] == "error"),
        },
        "results": results,
    }


# ── Export Configs API ───────────────────────────────────────────────────────
@app.get("/api/config/export")
async def api_config_export(tool: str = "", authorization: str | None = Header(None)):
    verify_master_key(authorization)
    if not tool:
        raise HTTPException(400, "Missing 'tool' query parameter")

    base_url = _get_base_url()
    api_key = config.master_key or ""
    top_models = [m for m in TOP_RECOMMENDED_MODELS if m in config.models][:10]
    default_model = top_models[0] if top_models else "llama-3.3-70b"

    configs: dict[str, dict[str, Any]] = {
        "openclaw": {
            "name": "OpenClaw",
            "config": {
                "api_key": api_key,
                "base_url": base_url,
                "default_model": default_model,
                "models": top_models,
            },
        },
        "cursor": {
            "name": "Cursor",
            "config": {
                "apiKey": api_key,
                "apiBase": base_url,
                "model": default_model,
            },
        },
        "librechat": {
            "name": "LibreChat",
            "config": {
                "endpoints": {
                    "custom": [{
                        "name": "Free LLM Gateway",
                        "apiKey": api_key,
                        "baseURL": base_url,
                        "models": {"default": [default_model], "fetch": True},
                    }]
                }
            },
        },
        "open-webui": {
            "name": "Open WebUI",
            "config": {
                "OPENAI_API_BASE_URL": base_url,
                "OPENAI_API_KEY": api_key,
                "model": default_model,
            },
        },
        "continue-dev": {
            "name": "Continue.dev",
            "config": {
                "models": [{
                    "title": "Free LLM Gateway",
                    "provider": "openai",
                    "model": default_model,
                    "apiBase": base_url,
                    "apiKey": api_key,
                }],
            },
        },
        "jan": {
            "name": "Jan",
            "config": {
                "api_key": api_key,
                "base_url": base_url,
                "model": default_model,
            },
        },
        "litellm": {
            "name": "LiteLLM",
            "config": {
                "model_list": [
                    {
                        "model_name": m,
                        "litellm_params": {
                            "model": f"openai/{m}",
                            "api_base": base_url,
                            "api_key": api_key,
                        },
                    }
                    for m in top_models[:5]
                ],
            },
        },
        "generic-openai": {
            "name": "Generic OpenAI SDK",
            "config": {
                "api_key": api_key,
                "base_url": base_url,
                "default_model": default_model,
                "models": top_models,
                "env_vars": {
                    "OPENAI_API_KEY": api_key,
                    "OPENAI_BASE_URL": base_url,
                },
            },
        },
    }

    if tool not in configs:
        raise HTTPException(400, f"Unknown tool: {tool}. Supported: {', '.join(configs.keys())}")

    return {"tool": tool, **configs[tool]}


@app.get("/api/config/export/tools")
async def api_config_export_tools():
    """List all supported export tool names."""
    return {
        "tools": [
            {"id": "openclaw", "name": "OpenClaw"},
            {"id": "cursor", "name": "Cursor"},
            {"id": "librechat", "name": "LibreChat"},
            {"id": "open-webui", "name": "Open WebUI"},
            {"id": "continue-dev", "name": "Continue.dev"},
            {"id": "jan", "name": "Jan"},
            {"id": "litellm", "name": "LiteLLM"},
            {"id": "generic-openai", "name": "Generic OpenAI SDK"},
        ]
    }


@app.post("/v1/batch")
async def batch_requests(request: Request, authorization: str | None = Header(None)):
    """Execute multiple chat completion requests in parallel.

    Body: {"requests": [{"model": "...", "messages": [...]}, ...]}
    Returns results as array in the same order. Each item is independent.
    """
    verify_master_key(authorization)
    body = await request.json()
    requests_list = body.get("requests", [])

    if not requests_list:
        return {"object": "batch", "results": []}

    if len(requests_list) > BATCH_MAX_SIZE:
        raise HTTPException(
            400,
            f"Batch size {len(requests_list)} exceeds maximum of {BATCH_MAX_SIZE}",
        )

    async def _process_batch_item(idx: int, req: dict) -> dict:
        model = req.get("model", "")
        if not model:
            return {
                "index": idx, "success": False,
                "error": "Missing 'model' field",
            }

        # Smart routing for each item
        resolved = smart_router.resolve(model)
        resolved_model = resolved.resolved_name

        # Tool auto-routing
        if has_tool_calling(req):
            model_cfg = config.models.get(resolved_model)
            if model_cfg and not model_cfg.capabilities.supports_tools:
                alt = smart_router.find_model_with_capability("supports_tools")
                if alt:
                    resolved_model = alt

        # Disable streaming in batch — not useful here
        batch_req = {**req, "stream": False, "model": resolved_model}

        try:
            result, provider, provider_model = await router.route_request(
                resolved_model, batch_req, _client,
            )
            if isinstance(result, dict):
                result.setdefault("model", provider_model)
            return {
                "index": idx, "success": True,
                "result": result, "provider": provider,
                "provider_model": provider_model,
                "substitution": resolved.substitution,
            }
        except Exception as e:
            return {"index": idx, "success": False, "error": str(e)[:500]}

    results = await asyncio.gather(
        *[_process_batch_item(i, req) for i, req in enumerate(requests_list)]
    )
    return {"object": "batch", "results": list(results)}


# ── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=config.host,
        port=config.port,
        reload=False,
        log_level="info",
    )
