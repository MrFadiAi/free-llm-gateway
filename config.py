"""Configuration loading from .env and models.yaml."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

BASE_DIR = Path(__file__).parent


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    api_keys: list[str] = field(default_factory=list)
    rpm_limit: int = 0  # 0 = unlimited
    rpd_limit: int = 0  # 0 = unlimited

    def __post_init__(self) -> None:
        self._key_index: int = 0

    @property
    def api_key(self) -> str:
        """Current active API key (round-robin)."""
        if not self.api_keys:
            return ""
        return self.api_keys[self._key_index % len(self.api_keys)]

    @api_key.setter
    def api_key(self, value: str) -> None:
        """Allow setting a single key (backward compatibility)."""
        if value:
            if not self.api_keys:
                self.api_keys = [value]
            elif value not in self.api_keys:
                self.api_keys[self.active_key_index] = value

    @property
    def active_key_index(self) -> int:
        return self._key_index % len(self.api_keys) if self.api_keys else 0

    @property
    def total_keys(self) -> int:
        return len(self.api_keys)

    def rotate_key(self) -> str:
        """Advance to next key (round-robin) and return it."""
        if self.api_keys:
            self._key_index = (self._key_index + 1) % len(self.api_keys)
        return self.api_key


@dataclass
class ModelFallback:
    provider: str
    model: str


@dataclass
class ModelCapabilities:
    supports_tools: bool = False
    supports_vision: bool = False
    supports_streaming: bool = True


@dataclass
class ModelConfig:
    unified_name: str
    fallbacks: list[ModelFallback] = field(default_factory=list)
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)


@dataclass
class AppConfig:
    master_key: str
    host: str
    port: int
    default_rpm_limit: int
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    models: dict[str, ModelConfig] = field(default_factory=dict)


# Provider definitions: name -> (env_key_for_api_key, base_url_template)
PROVIDER_DEFS: dict[str, tuple[str, str]] = {
    "openrouter": ("OPENROUTER_KEY", "https://openrouter.ai/api/v1"),
    "github": ("GITHUB_KEY", "https://models.inference.ai.azure.com"),
    "groq": ("GROQ_KEY", "https://api.groq.com/openai/v1"),
    "cerebras": ("CEREBRAS_KEY", "https://api.cerebras.ai/v1"),
    "cloudflare": (
        "CLOUDFLARE_KEY",
        "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
    ),
    "huggingface": ("HUGGINGFACE_KEY", "https://api-inference.huggingface.co/models"),
    "nvidia": ("NVIDIA_KEY", "https://integrate.api.nvidia.com/v1"),
    "siliconflow": ("SILICONFLOW_KEY", "https://api.siliconflow.cn/v1"),
    "cohere": ("COHERE_KEY", "https://api.cohere.com/v2"),
    "google_gemini": ("GOOGLE_GEMINI_KEY", "https://generativelanguage.googleapis.com/v1beta"),
    "mistral": ("MISTRAL_KEY", "https://api.mistral.ai/v1"),
    "kilo": ("KILO_KEY", "https://api.kilo.ai/api/gateway"),
    "llm7": ("LLM7_KEY", "https://api.llm7.io/v1"),
    "ollama": ("OLLAMA_KEY", "https://api.ollama.com"),
}

# Providers that use OpenAI-compatible chat/completions endpoints
OPENAI_COMPATIBLE = {
    "openrouter", "github", "groq", "cerebras", "nvidia",
    "siliconflow", "mistral", "llm7", "ollama",
}

# Providers needing special request formatting
SPECIAL_PROVIDERS = {"cloudflare", "huggingface", "cohere", "google_gemini", "kilo"}


def _load_provider_keys(env_key: str) -> list[str]:
    """Load API keys for a provider.

    Supports:
      - Comma-separated: OPENROUTER_KEY="k1,k2,k3"
      - Indexed: OPENROUTER_KEY_1="k1", OPENROUTER_KEY_2="k2"
    """
    keys: list[str] = []
    # Comma-separated keys
    raw = os.environ.get(env_key, "")
    if raw:
        keys.extend(k.strip() for k in raw.split(",") if k.strip())
    # Indexed keys (KEY_1, KEY_2, ... up to KEY_10)
    for i in range(1, 11):
        indexed = os.environ.get(f"{env_key}_{i}", "")
        if indexed and indexed not in keys:
            keys.append(indexed.strip())
    return keys


def _load_providers() -> dict[str, ProviderConfig]:
    providers: dict[str, ProviderConfig] = {}
    for name, (env_key, base_url_tpl) in PROVIDER_DEFS.items():
        api_keys = _load_provider_keys(env_key)
        base_url = base_url_tpl
        if "{account_id}" in base_url:
            account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
            base_url = base_url.replace("{account_id}", account_id)
        providers[name] = ProviderConfig(
            name=name,
            base_url=base_url,
            api_keys=api_keys,
            rpm_limit=int(os.environ.get("DEFAULT_RPM_LIMIT", "0")),
        )
    return providers


def _load_models() -> dict[str, ModelConfig]:
    models_file = BASE_DIR / "models.yaml"
    if not models_file.exists():
        return {}

    with open(models_file) as f:
        data = yaml.safe_load(f) or {}

    models: dict[str, ModelConfig] = {}
    for model_name, model_data in data.get("models", {}).items():
        if isinstance(model_data, list):
            # Old format: list of fallbacks directly
            fb_list = [
                ModelFallback(provider=fb["provider"], model=fb["model"])
                for fb in model_data
            ]
            capabilities = ModelCapabilities()
        elif isinstance(model_data, dict):
            # New format: {capabilities: {...}, fallbacks: [...]}
            fb_list = [
                ModelFallback(provider=fb["provider"], model=fb["model"])
                for fb in model_data.get("fallbacks", [])
            ]
            caps_data = model_data.get("capabilities", {})
            capabilities = ModelCapabilities(
                supports_tools=caps_data.get("supports_tools", False),
                supports_vision=caps_data.get("supports_vision", False),
                supports_streaming=caps_data.get("supports_streaming", True),
            )
        else:
            continue
        models[model_name] = ModelConfig(
            unified_name=model_name, fallbacks=fb_list, capabilities=capabilities,
        )
    return models


def load_config() -> AppConfig:
    return AppConfig(
        master_key=os.environ.get("MASTER_KEY", ""),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        default_rpm_limit=int(os.environ.get("DEFAULT_RPM_LIMIT", "0")),
        providers=_load_providers(),
        models=_load_models(),
    )


async def discover_models(
    client: httpx.AsyncClient, config: AppConfig
) -> dict[str, ModelConfig]:
    """Query each provider with an API key for available models and merge into config.

    Models from models.yaml take priority. Auto-discovered models are added only
    if no manual definition exists. Returns the set of newly discovered models.
    """
    discovered: dict[str, ModelConfig] = {}
    existing_names = set(config.models.keys())

    for name, provider in config.providers.items():
        if not provider.api_key:
            continue

        models = await _fetch_provider_models(client, provider)
        if not models:
            continue

        for m in models:
            model_id = m.get("id", "")
            if not model_id or model_id in existing_names:
                continue

            # Filter: only include free models per provider
            if not _is_free_model(name, model_id, m):
                continue

            # Create a unified model name: provider/model-id
            unified = model_id
            fb = ModelFallback(provider=name, model=model_id)
            discovered[unified] = ModelConfig(
                unified_name=unified,
                fallbacks=[fb],
            )
            existing_names.add(unified)

    # Merge into config (manual definitions always win)
    for unified_name, model_cfg in discovered.items():
        if unified_name not in config.models:
            config.models[unified_name] = model_cfg

    logger.info(
        "Auto-discovered %d new models from %d providers",
        len(discovered),
        sum(1 for p in config.providers.values() if p.api_key),
    )
    return discovered


def _is_free_model(provider: str, model_id: str, model_data: dict) -> bool:
    """Check if a model is available on a free tier.
    
    Rules per provider:
    - OpenRouter: must have ':free' suffix
    - NVIDIA: all models free (free developer tier)
    - Groq: all models free (free tier)
    - Cerebras: all models free (free tier)
    - GitHub Models: all models free (free tier)
    - Mistral: check pricing field or skip (experiment plan)
    - Cohere: trial plan (1000 calls/month)
    - Google Gemini: free tier models only
    - Others: include by default
    """
    # OpenRouter: only free models have ':free' suffix
    if provider == "openrouter":
        return model_id.endswith(":free")
    
    # These providers offer all listed models on free tiers
    FREE_PROVIDERS = {"nvidia", "groq", "cerebras", "github", "llm7", "ollama"}
    if provider in FREE_PROVIDERS:
        return True
    
    # Mistral: experiment plan includes these models
    if provider == "mistral":
        return True  # All listed on /v1/models are on experiment plan
    
    # Cohere: trial key gives 1000 calls/month
    if provider == "cohere":
        return True
    
    # Google Gemini: Flash/Lite models are free, Pro may have limits
    if provider == "google_gemini":
        model_name = model_id.lower()
        return "flash" in model_name or "lite" in model_name or "pro" in model_name
    
    # SiliconFlow: permanently free models
    if provider == "siliconflow":
        return True  # Free models listed on their free tier
    
    # Cloudflare Workers AI: all free tier
    if provider == "cloudflare":
        return True
    
    # Default: include
    return True


async def _fetch_provider_models(
    client: httpx.AsyncClient, provider: ProviderConfig
) -> list[dict[str, Any]]:
    """Fetch available models from a provider's /models endpoint."""
    try:
        headers = {"Authorization": f"Bearer {provider.api_key}"}

        if provider.name in OPENAI_COMPATIBLE:
            url = f"{provider.base_url}/models"
        elif provider.name == "kilo":
            url = f"{provider.base_url}/v1/models"
        elif provider.name == "cloudflare":
            url = f"{provider.base_url}/models"
        elif provider.name == "google_gemini":
            url = f"{provider.base_url}/models?key={provider.api_key}"
            headers = {}
        elif provider.name == "cohere":
            url = f"{provider.base_url}/models"
        else:
            return []

        resp = await client.get(url, headers=headers, timeout=15.0)
        if resp.status_code >= 400:
            logger.debug("Provider %s models endpoint returned %d", provider.name, resp.status_code)
            return []

        data = resp.json()

        # OpenAI-compatible format: {"data": [...]}
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        # Gemini format: {"models": [...]}
        if isinstance(data, dict) and "models" in data:
            return data["models"]
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        logger.debug("Failed to fetch models from %s: %s", provider.name, e)
        return []
