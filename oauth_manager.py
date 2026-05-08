"""OAuth Manager — handles OAuth2 flows and auto token refresh.

Supports subscription-based providers (Claude Pro, Copilot, Cursor, etc.)
that use OAuth2 for authentication instead of static API keys.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

OAUTH_FILE = Path(__file__).parent / "data" / "oauth_tokens.json"

# OAuth endpoints for subscription providers
OAUTH_PROVIDERS: dict[str, dict[str, str]] = {
    "claude_pro": {
        "name": "Claude Pro",
        "authorize_url": "https://claude.ai/oauth/authorize",
        "token_url": "https://claude.ai/oauth/token",
        "scope": "chat:read chat:write",
        "redirect_uri": "http://localhost:8080/api/oauth/callback",
    },
    "github_copilot": {
        "name": "GitHub Copilot",
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "scope": "copilot",
        "redirect_uri": "http://localhost:8080/api/oauth/callback",
    },
    "cursor": {
        "name": "Cursor",
        "authorize_url": "https://cursor.sh/api/auth/oauth/authorize",
        "token_url": "https://cursor.sh/api/auth/oauth/token",
        "scope": "models:read models:write",
        "redirect_uri": "http://localhost:8080/api/oauth/callback",
    },
}


@dataclass
class OAuthToken:
    """Stored OAuth token with refresh capability."""
    provider: str
    access_token: str
    token_type: str = "Bearer"
    refresh_token: str = ""
    expires_at: float = 0.0  # Unix timestamp, 0 = never expires
    scope: str = ""
    obtained_at: float = 0.0

    @property
    def is_expired(self) -> bool:
        if self.expires_at <= 0:
            return False
        # Refresh 5 minutes early
        return time.time() >= (self.expires_at - 300)

    @property
    def can_refresh(self) -> bool:
        return bool(self.refresh_token)


class OAuthManager:
    """Manages OAuth2 tokens with auto-refresh."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._tokens: dict[str, OAuthToken] = {}
        self._pending_states: dict[str, dict[str, str]] = {}  # state -> provider info
        self._load()

    def _load(self) -> None:
        if OAUTH_FILE.exists():
            try:
                with open(OAUTH_FILE) as f:
                    data = json.load(f)
                for provider, tdata in data.get("tokens", {}).items():
                    self._tokens[provider] = OAuthToken(
                        provider=provider,
                        access_token=tdata.get("access_token", ""),
                        token_type=tdata.get("token_type", "Bearer"),
                        refresh_token=tdata.get("refresh_token", ""),
                        expires_at=tdata.get("expires_at", 0),
                        scope=tdata.get("scope", ""),
                        obtained_at=tdata.get("obtained_at", 0),
                    )
                logger.info("Loaded %d OAuth tokens", len(self._tokens))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Could not load OAuth tokens: %s", e)

    def _save(self) -> None:
        OAUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = {"tokens": {}}
            for provider, token in self._tokens.items():
                data["tokens"][provider] = {
                    "access_token": token.access_token,
                    "token_type": token.token_type,
                    "refresh_token": token.refresh_token,
                    "expires_at": token.expires_at,
                    "scope": token.scope,
                    "obtained_at": token.obtained_at,
                }
            with open(OAUTH_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            logger.warning("Could not save OAuth tokens: %s", e)

    def get_authorize_url(self, provider: str, client_id: str = "") -> dict[str, Any]:
        """Generate an OAuth authorization URL for a provider."""
        config = OAUTH_PROVIDERS.get(provider)
        if not config:
            return {"error": f"Unknown OAuth provider: {provider}"}

        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(48)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b"=").decode()

        self._pending_states[state] = {
            "provider": provider,
            "code_verifier": code_verifier,
            "client_id": client_id,
        }

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": config["redirect_uri"],
            "scope": config["scope"],
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        return {
            "provider": provider,
            "name": config["name"],
            "authorize_url": f"{config['authorize_url']}?{urlencode(params)}",
            "state": state,
            "callback_url": config["redirect_uri"],
        }

    async def handle_callback(
        self,
        state: str,
        code: str,
        client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        """Handle OAuth callback and exchange code for token."""
        pending = self._pending_states.pop(state, None)
        if not pending:
            return {"error": "Invalid or expired OAuth state"}

        provider = pending["provider"]
        config = OAUTH_PROVIDERS.get(provider)
        if not config:
            return {"error": f"Unknown provider: {provider}"}

        try:
            resp = await client.post(
                config["token_url"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": config["redirect_uri"],
                    "client_id": pending.get("client_id", ""),
                    "code_verifier": pending.get("code_verifier", ""),
                },
                headers={"Accept": "application/json"},
                timeout=30.0,
            )

            if resp.status_code >= 400:
                return {"error": f"Token exchange failed: HTTP {resp.status_code}"}

            data = resp.json()
            now = time.time()
            expires_in = data.get("expires_in", 3600)

            token = OAuthToken(
                provider=provider,
                access_token=data.get("access_token", ""),
                token_type=data.get("token_type", "Bearer"),
                refresh_token=data.get("refresh_token", ""),
                expires_at=now + expires_in if expires_in else 0,
                scope=data.get("scope", config["scope"]),
                obtained_at=now,
            )

            with self._lock:
                self._tokens[provider] = token
                self._save()

            return {
                "provider": provider,
                "status": "connected",
                "token_type": token.token_type,
                "expires_at": token.expires_at,
                "scope": token.scope,
            }
        except Exception as e:
            return {"error": f"OAuth callback failed: {e}"}

    async def refresh_token(
        self, provider: str, client: httpx.AsyncClient
    ) -> dict[str, Any]:
        """Refresh an expired token using the refresh token."""
        token = self._tokens.get(provider)
        if not token:
            return {"error": f"No token for provider: {provider}"}
        if not token.can_refresh:
            return {"error": "No refresh token available — re-authentication required"}

        config = OAUTH_PROVIDERS.get(provider)
        if not config:
            return {"error": f"Unknown provider: {provider}"}

        try:
            resp = await client.post(
                config["token_url"],
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": token.refresh_token,
                },
                headers={"Accept": "application/json"},
                timeout=30.0,
            )

            if resp.status_code >= 400:
                # Refresh failed — token may need re-auth
                return {"error": f"Refresh failed: HTTP {resp.status_code}", "needs_reauth": True}

            data = resp.json()
            now = time.time()
            expires_in = data.get("expires_in", 3600)

            token.access_token = data.get("access_token", token.access_token)
            token.refresh_token = data.get("refresh_token", token.refresh_token)
            token.expires_at = now + expires_in if expires_in else 0
            token.obtained_at = now

            with self._lock:
                self._save()

            return {
                "provider": provider,
                "status": "refreshed",
                "expires_at": token.expires_at,
            }
        except Exception as e:
            return {"error": f"Token refresh failed: {e}"}

    async def get_valid_token(
        self, provider: str, client: httpx.AsyncClient
    ) -> OAuthToken | None:
        """Get a valid (non-expired) token, refreshing if necessary."""
        token = self._tokens.get(provider)
        if not token:
            return None

        if not token.is_expired:
            return token

        if token.can_refresh:
            result = await self.refresh_token(provider, client)
            if "error" not in result:
                return self._tokens.get(provider)

        return None

    def remove_token(self, provider: str) -> bool:
        with self._lock:
            if provider not in self._tokens:
                return False
            del self._tokens[provider]
            self._save()
            return True

    def list_connections(self) -> list[dict[str, Any]]:
        """List all OAuth connections with status."""
        now = time.time()
        result = []
        for provider, config in OAUTH_PROVIDERS.items():
            token = self._tokens.get(provider)
            result.append({
                "provider": provider,
                "name": config["name"],
                "connected": token is not None,
                "expired": token.is_expired if token else False,
                "can_refresh": token.can_refresh if token else False,
                "expires_at": token.expires_at if token else 0,
                "scope": token.scope if token else "",
            })
        return result

    def get_token_value(self, provider: str) -> str | None:
        """Get the raw access token string for a provider (for use in API calls)."""
        token = self._tokens.get(provider)
        if token and not token.is_expired:
            return token.access_token
        return None


# Global singleton
oauth_manager = OAuthManager()
