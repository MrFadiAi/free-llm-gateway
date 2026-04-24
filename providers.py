"""Async provider adapters — sends requests to each LLM provider."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator

import httpx

from config import ProviderConfig, OPENAI_COMPATIBLE, SPECIAL_PROVIDERS

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 120.0  # seconds


class ProviderError(Exception):
    """Raised when a provider request fails."""

    def __init__(self, provider: str, status: int, message: str):
        self.provider = provider
        self.status = status
        self.message = message
        super().__init__(f"[{provider}] HTTP {status}: {message}")


def _is_rate_limited(status: int) -> bool:
    return status == 429


def _build_openai_headers(provider: ProviderConfig) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    if provider.name == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/free-llm-gateway"
        headers["X-Title"] = "Free LLM Gateway"
    return headers


def _build_openai_body(model: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize the request body for OpenAI-compatible providers."""
    body = {**payload}
    body["model"] = model
    return body


async def _request_openai_compatible(
    client: httpx.AsyncClient,
    provider: ProviderConfig,
    model: str,
    payload: dict[str, Any],
) -> dict[str, Any] | AsyncIterator[bytes]:
    url = f"{provider.base_url}/chat/completions"
    headers = _build_openai_headers(provider)
    body = _build_openai_body(model, payload)

    stream = payload.get("stream", False)

    if stream:
        return _stream_response(client, url, headers, body, provider.name)

    resp = await client.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
    if _is_rate_limited(resp.status_code):
        raise ProviderError(provider.name, 429, "Rate limited")
    if resp.status_code >= 400:
        raise ProviderError(provider.name, resp.status_code, resp.text[:500])

    return resp.json()


async def _stream_response(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    provider_name: str,
) -> AsyncIterator[bytes]:
    async with client.stream("POST", url, headers=headers, json=body, timeout=REQUEST_TIMEOUT) as resp:
        if _is_rate_limited(resp.status_code):
            raise ProviderError(provider_name, 429, "Rate limited")
        if resp.status_code >= 400:
            body_text = await resp.aread()
            raise ProviderError(provider_name, resp.status_code, body_text.decode()[:500])

        async for chunk in resp.aiter_bytes():
            yield chunk


async def _request_cloudflare(
    client: httpx.AsyncClient,
    provider: ProviderConfig,
    model: str,
    payload: dict[str, Any],
) -> dict[str, Any] | AsyncIterator[bytes]:
    url = f"{provider.base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    body = _build_openai_body(model, payload)

    stream = payload.get("stream", False)
    if stream:
        return _stream_response(client, url, headers, body, provider.name)

    resp = await client.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
    if _is_rate_limited(resp.status_code):
        raise ProviderError(provider.name, 429, "Rate limited")
    if resp.status_code >= 400:
        raise ProviderError(provider.name, resp.status_code, resp.text[:500])
    return resp.json()


async def _request_huggingface(
    client: httpx.AsyncClient,
    provider: ProviderConfig,
    model: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    url = f"{provider.base_url}/{model}"
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    # Convert OpenAI format to HF format
    messages = payload.get("messages", [])
    hf_body: dict[str, Any] = {"model": model}
    if messages:
        hf_body["messages"] = messages
    hf_body["max_tokens"] = payload.get("max_tokens", 1024)
    hf_body["stream"] = payload.get("stream", False)

    resp = await client.post(url, headers=headers, json=hf_body, timeout=REQUEST_TIMEOUT)
    if _is_rate_limited(resp.status_code):
        raise ProviderError(provider.name, 429, "Rate limited")
    if resp.status_code >= 400:
        raise ProviderError(provider.name, resp.status_code, resp.text[:500])
    return resp.json()


async def _request_cohere(
    client: httpx.AsyncClient,
    provider: ProviderConfig,
    model: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    url = f"{provider.base_url}/chat"
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    messages = payload.get("messages", [])
    # Convert to Cohere format
    cohere_body: dict[str, Any] = {"model": model}
    if messages:
        last_msg = messages[-1] if messages else {}
        cohere_body["message"] = last_msg.get("content", "")
        if len(messages) > 1:
            cohere_body["chat_history"] = [
                {"role": m["role"], "message": m["content"]}
                for m in messages[:-1]
                if m["role"] in ("user", "assistant")
            ]

    resp = await client.post(url, headers=headers, json=cohere_body, timeout=REQUEST_TIMEOUT)
    if _is_rate_limited(resp.status_code):
        raise ProviderError(provider.name, 429, "Rate limited")
    if resp.status_code >= 400:
        raise ProviderError(provider.name, resp.status_code, resp.text[:500])
    return resp.json()


async def _request_gemini(
    client: httpx.AsyncClient,
    provider: ProviderConfig,
    model: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    url = f"{provider.base_url}/models/{model}:generateContent?key={provider.api_key}"
    messages = payload.get("messages", [])
    contents = []
    for m in messages:
        role = "user" if m["role"] in ("user", "system") else "model"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})

    gemini_body: dict[str, Any] = {"contents": contents}
    resp = await client.post(url, json=gemini_body, timeout=REQUEST_TIMEOUT)
    if _is_rate_limited(resp.status_code):
        raise ProviderError(provider.name, 429, "Rate limited")
    if resp.status_code >= 400:
        raise ProviderError(provider.name, resp.status_code, resp.text[:500])
    return resp.json()


async def _request_kilo(
    client: httpx.AsyncClient,
    provider: ProviderConfig,
    model: str,
    payload: dict[str, Any],
) -> dict[str, Any] | AsyncIterator[bytes]:
    url = f"{provider.base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    body = _build_openai_body(model, payload)
    stream = payload.get("stream", False)

    if stream:
        return _stream_response(client, url, headers, body, provider.name)

    resp = await client.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
    if _is_rate_limited(resp.status_code):
        raise ProviderError(provider.name, 429, "Rate limited")
    if resp.status_code >= 400:
        raise ProviderError(provider.name, resp.status_code, resp.text[:500])
    return resp.json()


async def send_to_provider(
    client: httpx.AsyncClient,
    provider: ProviderConfig,
    model: str,
    payload: dict[str, Any],
) -> dict[str, Any] | AsyncIterator[bytes]:
    """Route a request to the correct provider adapter."""
    name = provider.name

    if name in OPENAI_COMPATIBLE:
        return await _request_openai_compatible(client, provider, model, payload)
    elif name == "cloudflare":
        return await _request_cloudflare(client, provider, model, payload)
    elif name == "huggingface":
        return await _request_huggingface(client, provider, model, payload)
    elif name == "cohere":
        return await _request_cohere(client, provider, model, payload)
    elif name == "google_gemini":
        return await _request_gemini(client, provider, model, payload)
    elif name == "kilo":
        return await _request_kilo(client, provider, model, payload)
    else:
        raise ProviderError(name, 400, f"Unknown provider: {name}")


async def fetch_provider_models(
    client: httpx.AsyncClient, provider: ProviderConfig
) -> list[dict[str, Any]]:
    """Fetch available models from a provider's /models endpoint."""
    if not provider.api_key:
        return []

    try:
        headers = _build_openai_headers(provider)
        url = f"{provider.base_url}/models"
        resp = await client.get(url, headers=headers, timeout=15.0)
        if resp.status_code >= 400:
            return []
        data = resp.json()
        return data.get("data", [])
    except Exception:
        return []
