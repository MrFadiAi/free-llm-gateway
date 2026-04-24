#!/usr/bin/env python3
"""Sync free models from awesome-free-llm-apis repo.

Pulls the latest data.json from the GitHub repo and:
1. Updates provider definitions in config
2. Updates models.yaml with all free models
3. Creates .env.example with new provider key slots
4. Tracks what changed

Usage:
  python3 sync_providers.py          # Sync and update
  python3 sync_providers.py --dry-run # Preview changes only
"""

import asyncio
import json
import os
import sys
import httpx
import yaml
from pathlib import Path
from datetime import datetime

DRY_RUN = "--dry-run" in sys.argv
HERE = Path(__file__).parent
MODELS_FILE = HERE / "models.yaml"
ENV_EXAMPLE = HERE / ".env.example"
DATA_DIR = HERE / "data"
STATE_FILE = DATA_DIR / "sync_state.json"

UPSTREAM_DATA_URL = "https://raw.githubusercontent.com/mnfst/awesome-free-llm-apis/main/data.json"
UPSTREAM_REPO = "https://github.com/mnfst/awesome-free-llm-apis"

# Map awesome-free-llm-apis provider names to our internal provider names
PROVIDER_NAME_MAP = {
    "Cohere": "cohere",
    "Google Gemini": "google_gemini",
    "Mistral AI": "mistral",
    "Zhipu (GLM)": "zhipu",
    "Cerebras": "cerebras",
    "Cloudflare Workers AI": "cloudflare",
    "GitHub Models": "github",
    "Groq": "groq",
    "Hugging Face": "huggingface",
    "Kilo AI": "kilo",
    "LLM7": "llm7",
    "NVIDIA": "nvidia",
    "Ollama Cloud": "ollama",
    "OpenRouter": "openrouter",
    "SiliconFlow": "siliconflow",
}

# Map provider names to their env key names
PROVIDER_ENV_KEYS = {
    "cohere": "COHERE_KEY",
    "google_gemini": "GOOGLE_GEMINI_KEY",
    "mistral": "MISTRAL_KEY",
    "zhipu": "ZHIPU_KEY",
    "cerebras": "CEREBRAS_KEY",
    "cloudflare": "CLOUDFLARE_KEY",
    "github": "GITHUB_KEY",
    "groq": "GROQ_KEY",
    "huggingface": "HUGGINGFACE_KEY",
    "kilo": "KILO_KEY",
    "llm7": "LLM7_KEY",
    "nvidia": "NVIDIA_KEY",
    "ollama": "OLLAMA_KEY",
    "openrouter": "OPENROUTER_KEY",
    "siliconflow": "SILICONFLOW_KEY",
}

# Models that are embeddings/reranking only (skip for chat)
SKIP_MODALITIES = {"Embeddings", "Reranking", "Embeddings (Text + Image)", "Image Generation"}


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_sync": None, "upstream_date": None, "providers_known": [], "sync_log": []}


def save_state(state):
    DATA_DIR.mkdir(exist_ok=True)
    state["last_sync"] = datetime.utcnow().isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2))


def load_yaml():
    if MODELS_FILE.exists():
        with open(MODELS_FILE) as f:
            return yaml.safe_load(f) or {}
    return {"models": {}}


def save_yaml(data):
    with open(MODELS_FILE, "w") as f:
        # Write header
        f.write("# Free LLM Gateway — Model definitions with provider fallback chains\n")
        f.write("# Auto-synced from awesome-free-llm-apis. Manual edits preserved.\n")
        f.write(f"# Last sync: {datetime.utcnow().isoformat()}\n\n")
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def update_env_example(providers_info):
    """Update .env.example with all known providers."""
    lines = [
        "# Free LLM Gateway Configuration",
        "# Copy this file to .env and fill in your API keys",
        "",
        "# Master key for authenticating to this gateway",
        "MASTER_KEY=your-master-key-here",
        "",
        "# Provider API Keys (get free keys from the links below)",
    ]

    for provider_name, info in providers_info.items():
        env_key = PROVIDER_ENV_KEYS.get(provider_name, f"{provider_name.upper()}_KEY")
        url = info.get("url", "")
        desc = info.get("description", "")
        lines.append(f"# {info.get('display_name', provider_name)}: {url}")
        if desc:
            lines.append(f"# {desc[:100]}")
        lines.append(f"{env_key}=")
        lines.append("")

    lines.extend([
        "# Cloudflare requires account ID",
        "CLOUDFLARE_ACCOUNT_ID=",
        "",
        "# Server settings",
        "HOST=0.0.0.0",
        "PORT=8080",
        "",
        "# Rate limit settings (requests per minute per provider, 0 = unlimited)",
        "DEFAULT_RPM_LIMIT=0",
        "",
        "# Cache settings",
        "CACHE_TTL=1800",
        "CACHE_MAX_SIZE=1000",
        "",
        "# Queue settings",
        "BATCH_MAX_SIZE=10",
    ])

    ENV_EXAMPLE.write_text("\n".join(lines))


async def fetch_upstream(client):
    """Fetch latest data.json from awesome-free-llm-apis."""
    try:
        resp = await client.get(UPSTREAM_DATA_URL, timeout=30.0)
        if resp.status_code < 400:
            return resp.json()
    except Exception as e:
        print(f"❌ Failed to fetch upstream data: {e}")
    return None


async def main():
    print("🔄 Free LLM Gateway — Provider Sync")
    print(f"   Source: {UPSTREAM_REPO}")
    print(f"   Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")
    print()

    state = load_state()
    yaml_data = load_yaml()
    existing_models = set(yaml_data.get("models", {}).keys())

    async with httpx.AsyncClient(http2=True, follow_redirects=True) as client:
        print("📡 Fetching latest provider data...")
        upstream = await fetch_upstream(client)

    if not upstream:
        print("❌ Could not fetch upstream data. Check internet connection.")
        return

    upstream_date = upstream.get("lastUpdated", "unknown")
    providers = upstream.get("providers", [])
    print(f"   Upstream date: {upstream_date}")
    print(f"   Providers found: {len(providers)}")
    print()

    # Process each provider
    providers_info = {}
    new_models = 0
    updated_models = 0
    skipped_models = 0

    for provider in providers:
        display_name = provider.get("name", "")
        base_url = provider.get("baseUrl", "")
        url = provider.get("url", "")
        description = provider.get("description", "")
        provider_models = provider.get("models", [])

        # Map to internal name
        internal_name = PROVIDER_NAME_MAP.get(display_name, "")
        if not internal_name:
            # Try to generate one from the display name
            internal_name = display_name.lower().replace(" ", "_").replace("(", "").replace(")", "")
            print(f"   ⚠ Unknown provider '{display_name}', using '{internal_name}'")

        providers_info[internal_name] = {
            "display_name": display_name,
            "base_url": base_url,
            "url": url,
            "description": description,
        }

        # Process models
        provider_new = 0
        for model in provider_models:
            model_id = model.get("id", "")
            model_name = model.get("name", "")
            modality = model.get("modality", "")
            context = model.get("context", "")
            rate_limit = model.get("rateLimit", "")

            # Skip non-chat models
            if modality in SKIP_MODALITIES:
                skipped_models += 1
                continue

            if not model_id:
                continue

            # Check if this model already exists in our config
            if model_id in existing_models:
                # Model exists — update fallback if needed
                model_cfg = yaml_data["models"][model_id]
                if isinstance(model_cfg, dict):
                    fallbacks = model_cfg.get("fallbacks", [])
                    provider_ids = [fb["provider"] for fb in fallbacks]
                    if internal_name not in provider_ids:
                        fallbacks.append({"provider": internal_name, "model": model_id})
                        updated_models += 1
                continue

            # New model — add it
            capabilities = {
                "supports_tools": True,
                "supports_vision": "Image" in modality or "Video" in modality,
                "supports_streaming": True,
            }

            yaml_data.setdefault("models", {})[model_id] = {
                "capabilities": capabilities,
                "fallbacks": [{"provider": internal_name, "model": model_id}],
                "_meta": {
                    "name": model_name,
                    "context": context,
                    "modality": modality,
                    "rate_limit": rate_limit,
                },
            }
            existing_models.add(model_id)
            new_models += 1
            provider_new += 1

        if provider_new:
            print(f"   ✨ {display_name}: {provider_new} new models")

    # Summary
    print()
    print(f"📊 Results:")
    print(f"   Providers processed: {len(providers)}")
    print(f"   New models added: {new_models}")
    print(f"   Existing models updated: {updated_models}")
    print(f"   Skipped (non-chat): {skipped_models}")
    print(f"   Total models in config: {len(yaml_data.get('models', {}))}")

    if DRY_RUN:
        print(f"\n📋 DRY RUN — no files modified")
        return

    # Save everything
    save_yaml(yaml_data)
    update_env_example(providers_info)

    state["upstream_date"] = upstream_date
    state["providers_known"] = list(providers_info.keys())
    state["sync_log"].append({
        "date": datetime.utcnow().isoformat(),
        "upstream_date": upstream_date,
        "new_models": new_models,
        "updated_models": updated_models,
        "total_models": len(yaml_data.get("models", {})),
    })
    # Keep only last 50 sync logs
    state["sync_log"] = state["sync_log"][-50:]
    save_state(state)

    print(f"\n✅ Sync complete!")
    print(f"   models.yaml updated")
    print(f"   .env.example updated with {len(providers_info)} providers")
    print(f"   Next step: add new API keys to .env and restart")


if __name__ == "__main__":
    asyncio.run(main())
