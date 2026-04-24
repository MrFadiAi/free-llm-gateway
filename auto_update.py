#!/usr/bin/env python3
"""Auto-update script for Free LLM Gateway.

Checks for new models from providers and updates models.yaml.
Can be run via cron or manually.

Usage:
  python3 auto_update.py          # Check and update
  python3 auto_update.py --dry-run # Preview changes only
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
DATA_DIR = HERE / "data"
STATE_FILE = DATA_DIR / "update_state.json"


def load_yaml():
    with open(MODELS_FILE) as f:
        return yaml.safe_load(f)


def save_yaml(data):
    with open(MODELS_FILE, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_update": None, "known_models": {}, "new_models_log": []}


def save_state(state):
    DATA_DIR.mkdir(exist_ok=True)
    state["last_update"] = datetime.utcnow().isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2))


# Provider model list endpoints
PROVIDER_ENDPOINTS = {
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/models",
        "headers": lambda key: {
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": "https://github.com/free-llm-gateway",
        },
        "parse": lambda data: [
            m["id"] for m in data.get("data", [])
            if ":free" in m.get("id", "")
        ],
    },
    "nvidia": {
        "url": "https://integrate.api.nvidia.com/v1/models",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
        "parse": lambda data: [m["id"] for m in data.get("data", [])],
    },
    "groq": {
        "url": "https://api.groq.com/openai/v1/models",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
        "parse": lambda data: [m["id"] for m in data.get("data", [])],
    },
    "github": {
        "url": "https://models.inference.ai.azure.com/models",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
        "parse": lambda data: [m["id"] for m in data.get("data", [])] if isinstance(data, dict) else [m.get("id","") for m in data],
    },
    "cerebras": {
        "url": "https://api.cerebras.ai/v1/models",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
        "parse": lambda data: [m["id"] for m in data.get("data", [])],
    },
    "mistral": {
        "url": "https://api.mistral.ai/v1/models",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
        "parse": lambda data: [m["id"] for m in data.get("data", [])],
    },
    "cohere": {
        "url": "https://api.cohere.com/v2/models",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
        "parse": lambda data: [m.get("name", m.get("id", "")) for m in data.get("models", data.get("data", []))],
    },
}


def get_provider_keys():
    """Load API keys from .env file."""
    env_file = HERE / ".env"
    keys = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if v and "KEY" in k.upper():
                    # Map env var names to provider names
                    provider = k.lower().replace("_key", "").replace("openrouter", "openrouter").replace("nvidia", "nvidia").replace("groq", "groq").replace("github", "github").replace("cerebras", "cerebras").replace("mistral", "mistral").replace("cohere", "cohere")
                    if provider in PROVIDER_ENDPOINTS:
                        keys[provider] = v.split(",")[0].strip()  # Take first key if comma-separated
    return keys


async def fetch_provider_models(client, provider, api_key):
    """Fetch model list from a provider."""
    cfg = PROVIDER_ENDPOINTS.get(provider)
    if not cfg or not api_key:
        return []
    try:
        resp = await client.get(
            cfg["url"],
            headers=cfg["headers"](api_key),
            timeout=15.0,
        )
        if resp.status_code < 400:
            return cfg["parse"](resp.json())
    except Exception as e:
        print(f"  ⚠ {provider}: {e}")
    return []


async def main():
    print("🔄 Free LLM Gateway — Auto-Update")
    print(f"   Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")
    print()

    yaml_data = load_yaml()
    state = load_state()
    existing_models = set(yaml_data.get("models", {}).keys())

    keys = get_provider_keys()
    if not keys:
        print("❌ No API keys found in .env")
        return

    async with httpx.AsyncClient(http2=True, follow_redirects=True) as client:
        all_new = {}

        for provider, api_key in keys.items():
            print(f"📡 Checking {provider}...")
            models = await fetch_provider_models(client, provider, api_key)
            print(f"   Found {len(models)} models")

            known = set(state["known_models"].get(provider, []))
            new_models = [m for m in models if m not in known]

            if new_models:
                print(f"   ✨ {len(new_models)} new models: {', '.join(new_models[:5])}{'...' if len(new_models) > 5 else ''}")
                all_new[provider] = new_models

                if not DRY_RUN:
                    state["known_models"][provider] = models

                    # Auto-add new models to YAML
                    for model_id in new_models:
                        # Skip if already exists under a different name
                        if model_id not in yaml_data.get("models", {}):
                            yaml_data.setdefault("models", {})[model_id] = {
                                "capabilities": {
                                    "supports_tools": True,
                                    "supports_vision": False,
                                    "supports_streaming": True,
                                },
                                "fallbacks": [
                                    {"provider": provider, "model": model_id}
                                ],
                            }
                            state["new_models_log"].append({
                                "date": datetime.utcnow().isoformat(),
                                "provider": provider,
                                "model": model_id,
                            })
            else:
                if not DRY_RUN:
                    state["known_models"][provider] = models

    # Save
    if not DRY_RUN:
        if all_new:
            save_yaml(yaml_data)
            print(f"\n✅ Updated models.yaml with {sum(len(v) for v in all_new.values())} new models")
        else:
            print("\n✅ All models up to date — no changes needed")
        save_state(state)
    else:
        if all_new:
            print(f"\n📋 Would add {sum(len(v) for v in all_new.values())} new models:")
            for p, ms in all_new.items():
                for m in ms:
                    print(f"   + {p}/{m}")
        else:
            print("\n✅ All models up to date")

    print(f"\n📊 Total models in config: {len(yaml_data.get('models', {}))}")
    print(f"   Last update: {state.get('last_update', 'never')}")


if __name__ == "__main__":
    asyncio.run(main())
