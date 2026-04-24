"""Configuration loading from .env and models.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    rpm_limit: int = 0  # 0 = unlimited
    rpd_limit: int = 0  # 0 = unlimited


@dataclass
class ModelFallback:
    provider: str
    model: str


@dataclass
class ModelConfig:
    unified_name: str
    fallbacks: list[ModelFallback] = field(default_factory=list)


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


def _load_providers() -> dict[str, ProviderConfig]:
    providers: dict[str, ProviderConfig] = {}
    for name, (env_key, base_url_tpl) in PROVIDER_DEFS.items():
        api_key = os.environ.get(env_key, "")
        base_url = base_url_tpl
        if "{account_id}" in base_url:
            account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
            base_url = base_url.replace("{account_id}", account_id)
        providers[name] = ProviderConfig(
            name=name,
            base_url=base_url,
            api_key=api_key,
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
    for model_name, fallbacks in data.get("models", {}).items():
        fb_list = [
            ModelFallback(provider=fb["provider"], model=fb["model"])
            for fb in fallbacks
        ]
        models[model_name] = ModelConfig(unified_name=model_name, fallbacks=fb_list)
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
