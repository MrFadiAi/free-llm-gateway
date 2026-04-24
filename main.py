"""Free LLM Gateway — unified OpenAI-compatible API server."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from config import load_config, AppConfig
from rate_limiter import RateLimiter
from router import Router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ── Globals ──────────────────────────────────────────────────────────────────
config: AppConfig = load_config()
rate_limiter = RateLimiter()
router = Router(config, rate_limiter)
templates = Jinja2Templates(directory="templates")

# Shared httpx client (reused across requests for connection pooling)
_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client
    _client = httpx.AsyncClient(http2=True, follow_redirects=True)
    logger.info(
        "Gateway started — %d models, %d providers configured",
        len(config.models),
        sum(1 for p in config.providers.values() if p.api_key),
    )
    yield
    if _client:
        await _client.aclose()


app = FastAPI(title="Free LLM Gateway", version="1.0.0", lifespan=lifespan)


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

    result, provider, provider_model = await router.route_request(model, body, _client)

    if stream and hasattr(result, "__aiter__"):
        return StreamingResponse(
            result,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Provider": provider,
                "X-Provider-Model": provider_model,
            },
        )

    # Non-streaming: ensure it's a dict
    if isinstance(result, dict):
        result.setdefault("model", provider_model)
        return result
    return result


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


# ── Dashboard ────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    model_status = router.get_model_status()
    rate_status = rate_limiter.get_all_status()
    logs = router.get_logs(50)
    providers_with_keys = {
        name: bool(p.api_key) for name, p in config.providers.items()
    }
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "models": model_status,
        "rate_limits": rate_status,
        "logs": logs,
        "providers": providers_with_keys,
        "total_models": len(model_status),
        "total_providers": sum(1 for v in providers_with_keys.values() if v),
    })


@app.get("/api/status")
async def api_status():
    return {
        "models": router.get_model_status(),
        "rate_limits": rate_limiter.get_all_status(),
        "logs": router.get_logs(50),
    }


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
