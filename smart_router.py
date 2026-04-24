"""Smart routing — model aliases, equivalence mapping, and capability-based fallback."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ── Model aliases: common names → canonical gateway model names ──────────────

MODEL_ALIASES: dict[str, str] = {
    # GPT family
    "gpt4": "gpt-oss-120b",
    "gpt-4": "gpt-oss-120b",
    "chatgpt-4": "gpt-oss-120b",
    "gpt-4o": "gpt-oss-120b",
    "gpt-4-turbo": "gpt-oss-120b",
    "gpt4o": "gpt-oss-120b",
    "chatgpt": "gpt-oss-120b",
    # Claude family → best available equivalent
    "claude": "nemotron-super-120b",
    "claude-3": "nemotron-super-120b",
    "claude-3.5": "nemotron-super-120b",
    "claude-3.5-sonnet": "nemotron-super-120b",
    "claude-3-opus": "nemotron-super-120b",
    "claude-sonnet": "nemotron-super-120b",
    "claude-opus": "nemotron-super-120b",
    # Llama family
    "llama": "llama-3.3-70b",
    "llama-3": "llama-3.3-70b",
    "llama3": "llama-3.3-70b",
    "llama3-70b": "llama-3.3-70b",
    "llama3.3": "llama-3.3-70b",
    "llama-3.1": "llama-3.3-70b",
    # DeepSeek
    "deepseek": "deepseek-r1",
    "deepseek-chat": "deepseek-r1",
    "deepseek-v3": "deepseek-r1",
    # Mistral
    "mistral": "mistral-large",
    "mistral-7b": "mistral-large",
    "mixtral": "mistral-large",
    "mistral-medium": "mistral-large",
    # Gemma
    "gemma": "gemma-4-31b",
    "gemma-2": "gemma-4-31b",
    # Qwen
    "qwen": "qwen3-coder",
    "qwen2": "qwen3-coder",
    "qwen2.5": "qwen3-coder",
    "qwen-coder": "qwen3-coder",
    # Nemotron
    "nemotron": "nemotron-super-120b",
    # Gemini → best available
    "gemini": "gemma-4-31b",
    "gemini-pro": "gemma-4-31b",
    "gemini-1.5": "gemma-4-31b",
}

# ── Model tiers for equivalence matching ─────────────────────────────────────

MODEL_TIERS: dict[str, list[str]] = {
    "frontier": [
        "nemotron-super-120b", "gpt-oss-120b", "deepseek-r1",
        "qwen3-next-80b", "mistral-large", "hermes-3-405b",
        "minimax-m2.5", "llama-3.3-70b",
    ],
    "fast": [
        "llama-3.1-8b", "gemma-3-4b", "gemma-3-12b",
        "nemotron-nano-30b", "gpt-oss-20b",
    ],
    "code": ["qwen3-coder"],
    "reasoning": ["deepseek-r1"],
    "vision": ["gemma-4-31b", "gemma-4-26b", "gemma-3-27b"],
    "chat": [
        "llama-3.3-70b", "gemma-4-31b", "gemma-4-26b",
        "gemma-3-27b", "gemma-3-12b", "gemma-3-4b",
        "nemotron-super-120b", "nemotron-nano-30b",
        "gpt-oss-120b", "gpt-oss-20b",
        "qwen3-next-80b", "minimax-m2.5",
        "glm-4.5-air", "hermes-3-405b",
        "mistral-large", "ling-2.6-flash",
        "dolphin-mistral-24b",
    ],
    "embedding": ["text-embedding-3-small"],
}

# ── Model families (prefix-based grouping) ───────────────────────────────────

MODEL_FAMILIES: dict[str, list[str]] = {
    "llama": ["llama-3.3-70b", "llama-3.1-8b"],
    "gemma": ["gemma-4-31b", "gemma-4-26b", "gemma-3-27b", "gemma-3-12b", "gemma-3-4b"],
    "nemotron": ["nemotron-super-120b", "nemotron-nano-30b"],
    "gpt": ["gpt-oss-120b", "gpt-oss-20b"],
    "qwen": ["qwen3-coder", "qwen3-next-80b"],
    "deepseek": ["deepseek-r1"],
    "mistral": ["mistral-large", "dolphin-mistral-24b"],
    "minimax": ["minimax-m2.5"],
    "glm": ["glm-4.5-air"],
    "hermes": ["hermes-3-405b"],
}


@dataclass
class ResolveResult:
    """Result of model name resolution."""
    original_name: str
    resolved_name: str
    alias_used: str | None = None
    substitution: str | None = None


class SmartRouter:
    """Handles model name resolution, aliases, and capability-based routing."""

    def __init__(self, available_models: dict[str, Any]) -> None:
        self._available = available_models

    def resolve(self, name: str) -> ResolveResult:
        """Resolve a model name through alias lookup and equivalence matching.

        Steps:
        1. Exact match in available models
        2. Alias lookup (case-insensitive)
        3. Prefix / family matching
        4. Tier-based fallback
        """
        # Step 1: Exact match
        if name in self._available:
            return ResolveResult(original_name=name, resolved_name=name)

        # Step 2: Alias lookup (case-insensitive)
        name_lower = name.lower().strip()
        if name_lower in MODEL_ALIASES:
            resolved = MODEL_ALIASES[name_lower]
            if resolved in self._available:
                return ResolveResult(
                    original_name=name,
                    resolved_name=resolved,
                    alias_used=name_lower,
                )

        # Step 3: Prefix / family matching
        prefix_match = self._find_by_prefix(name_lower)
        if prefix_match:
            return ResolveResult(
                original_name=name,
                resolved_name=prefix_match,
                substitution=f"'{name}' not found -> using '{prefix_match}' (prefix match)",
            )

        # Step 4: Tier-based fallback
        equivalent = self._find_equivalent(name_lower)
        if equivalent:
            return ResolveResult(
                original_name=name,
                resolved_name=equivalent,
                substitution=f"'{name}' not found -> using '{equivalent}' (equivalent model)",
            )

        return ResolveResult(original_name=name, resolved_name=name)

    def _find_by_prefix(self, name: str) -> str | None:
        """Find a model by prefix matching on family names and model names."""
        for family_name, models in MODEL_FAMILIES.items():
            if name.startswith(family_name) or family_name.startswith(name):
                for model in models:
                    if model in self._available:
                        return model

        for model_name in self._available:
            ml = model_name.lower()
            if ml.startswith(name) or name.startswith(ml):
                return model_name
        return None

    def _find_equivalent(self, name: str) -> str | None:
        """Find an equivalent model based on tier matching."""
        for tier_models in MODEL_TIERS.values():
            for model in tier_models:
                if name in model.lower() or model.lower() in name:
                    for available in tier_models:
                        if available in self._available:
                            return available

        # Default: first available frontier model
        for model in MODEL_TIERS.get("frontier", []):
            if model in self._available:
                return model

        if self._available:
            return next(iter(self._available))
        return None

    def find_model_with_capability(
        self, capability: str, prefer_models: list[str] | None = None,
    ) -> str | None:
        """Find a model that supports a given capability.

        Args:
            capability: One of 'supports_tools', 'supports_vision', 'supports_streaming'.
            prefer_models: Optional list of model names to check first.
        """
        if prefer_models:
            for model_name in prefer_models:
                if model_name in self._available:
                    if self._has_capability(model_name, capability):
                        return model_name

        for model_name in self._available:
            if self._has_capability(model_name, capability):
                return model_name
        return None

    def _has_capability(self, model_name: str, capability: str) -> bool:
        model_cfg = self._available.get(model_name)
        if model_cfg and hasattr(model_cfg, "capabilities"):
            return bool(getattr(model_cfg.capabilities, capability, False))
        return False
