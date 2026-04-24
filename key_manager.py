"""Encrypted API key management with Fernet encryption."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

KEYS_FILE = Path(__file__).parent / "data" / "keys.json"


def _derive_fernet_key(secret: str) -> bytes:
    """Derive a valid Fernet key from an arbitrary secret string."""
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


class KeyManager:
    """Manages encrypted provider API keys stored in data/keys.json."""

    def __init__(self, master_key: str) -> None:
        self._fernet = Fernet(_derive_fernet_key(master_key))
        self._keys: dict[str, list[dict[str, Any]]] = {}
        self._load()

    def _load(self) -> None:
        if not KEYS_FILE.exists():
            self._keys = {}
            return
        try:
            raw = json.loads(KEYS_FILE.read_text())
            for provider, entries in raw.items():
                self._keys[provider] = []
                for entry in entries:
                    decrypted = self._fernet.decrypt(entry["key"].encode()).decode()
                    self._keys[provider].append({
                        "key": decrypted,
                        "validated": entry.get("validated", False),
                        "added_at": entry.get("added_at", ""),
                    })
            logger.info("Loaded %d provider keys from storage", sum(len(v) for v in self._keys.values()))
        except Exception as e:
            logger.warning("Failed to load keys: %s", e)
            self._keys = {}

    def _save(self) -> None:
        KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, list[dict[str, Any]]] = {}
        for provider, entries in self._keys.items():
            data[provider] = []
            for entry in entries:
                encrypted = self._fernet.encrypt(entry["key"].encode()).decode()
                data[provider].append({
                    "key": encrypted,
                    "validated": entry.get("validated", False),
                    "added_at": entry.get("added_at", ""),
                })
        KEYS_FILE.write_text(json.dumps(data, indent=2))

    def add_key(self, provider: str, api_key: str) -> int:
        """Add a key for a provider. Returns the index of the new key."""
        if provider not in self._keys:
            self._keys[provider] = []
        from datetime import datetime
        entry = {
            "key": api_key,
            "validated": False,
            "added_at": datetime.utcnow().isoformat(),
        }
        self._keys[provider].append(entry)
        self._save()
        return len(self._keys[provider]) - 1

    def remove_key(self, provider: str, index: int) -> bool:
        """Remove a key by provider and index. Returns True if removed."""
        entries = self._keys.get(provider, [])
        if index < 0 or index >= len(entries):
            return False
        entries.pop(index)
        if not entries:
            del self._keys[provider]
        self._save()
        return True

    def list_keys(self) -> dict[str, list[dict[str, Any]]]:
        """List all keys with masked values."""
        result: dict[str, list[dict[str, Any]]] = {}
        for provider, entries in self._keys.items():
            result[provider] = []
            for i, entry in enumerate(entries):
                key = entry["key"]
                masked = "****" + key[-4:] if len(key) > 4 else "****"
                result[provider].append({
                    "index": i,
                    "key_masked": masked,
                    "validated": entry.get("validated", False),
                    "added_at": entry.get("added_at", ""),
                })
        return result

    def get_keys(self, provider: str) -> list[str]:
        """Get all raw API keys for a provider."""
        return [e["key"] for e in self._keys.get(provider, [])]

    def get_first_key(self, provider: str) -> str | None:
        """Get the first available key for a provider."""
        keys = self.get_keys(provider)
        return keys[0] if keys else None

    def set_validated(self, provider: str, index: int, valid: bool) -> None:
        """Update validation status of a key."""
        entries = self._keys.get(provider, [])
        if 0 <= index < len(entries):
            entries[index]["validated"] = valid
            self._save()

    def has_provider(self, provider: str) -> bool:
        return bool(self._keys.get(provider))
