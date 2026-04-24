# Free LLM Gateway 🔑

A unified OpenAI-compatible API server that aggregates **14+ free LLM providers** into one endpoint. Configure your free API keys in `.env`, then use **one base URL + one master key** to access every model.

[**العربية**](README_AR.md) | **English**

## Features

- **Single endpoint** — `http://localhost:8080/v1` (OpenAI SDK-compatible)
- **14+ providers** — OpenRouter, GitHub Models, Groq, Cerebras, Cloudflare, HuggingFace, NVIDIA, SiliconFlow, Cohere, Google Gemini, Mistral, Kilo, LLM7, Ollama Cloud
- **260+ free models** — auto-discovered from all providers
- **Automatic fallback** — if one provider fails (rate limit, error, timeout), tries the next
- **Round-robin load balancing** — distributes requests across providers
- **Rate limit tracking** — per-provider monitoring with auto-rotation
- **Streaming support** — full SSE passthrough
- **Web dashboard** — live status, analytics, key management at `http://localhost:8080/`
- **Auto-sync** — pulls new free models from [awesome-free-llm-apis](https://github.com/mnfst/awesome-free-llm-apis)
- **Analytics** — usage tracking, estimated savings, provider success rates
- **Key health validation** — one-click test all API keys
- **Smart routing** — 60+ model aliases (type "gpt-4" → best available model)
- **Batch requests** — fan out multiple requests in parallel
- **Docker support** — one command to deploy

## Quick Start

```bash
# 1. Clone
git clone https://github.com/MrFadiAi/free-llm-gateway.git
cd free-llm-gateway

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure API keys
cp .env.example .env
# Edit .env — add at least one provider API key

# 4. Start the gateway
python main.py
```

Open `http://127.0.0.1:8080/` for the dashboard.

## Configuration

### Environment Variables (`.env`)

| Variable | Description | Get Free Key |
|---|---|---|
| `MASTER_KEY` | Your gateway API key | Set anything you want |
| `OPENROUTER_KEY` | Largest free model catalog | [openrouter.ai ↗](https://openrouter.ai/keys) |
| `NVIDIA_KEY` | 100+ models, no daily cap | [build.nvidia.com ↗](https://build.nvidia.com/) |
| `GITHUB_KEY` | OpenAI, Meta, Mistral models | [GitHub Tokens ↗](https://github.com/settings/tokens) |
| `GROQ_KEY` | Ultra-fast inference | [console.groq.com ↗](https://console.groq.com/) |
| `CEREBRAS_KEY` | Fastest Llama inference (~2,600 tok/s) | [cloud.cerebras.ai ↗](https://cloud.cerebras.ai/) |
| `GOOGLE_GEMINI_KEY` | Gemini 2.5 Flash, 1M context | [aistudio.google.com ↗](https://aistudio.google.com/apikey) |
| `MISTRAL_KEY` | Mistral Small/Large/Codestral | [console.mistral.ai ↗](https://console.mistral.ai/) |
| `COHERE_KEY` | Command R+ (1K calls/month) | [dashboard.cohere.com ↗](https://dashboard.cohere.com/) |
| `SILICONFLOW_KEY` | Qwen, DeepSeek, GLM | [siliconflow.cn ↗](https://siliconflow.cn/) |
| `HUGGINGFACE_KEY` | Thousands of community models | [huggingface.co ↗](https://huggingface.co/settings/tokens) |
| `CLOUDFLARE_KEY` | 50+ models, 10K neurons/day | [Cloudflare Workers AI ↗](https://dash.cloudflare.com/) |
| `KILO_KEY` | Free model gateway | [kilo.ai ↗](https://kilo.ai/) |
| `LLM7_KEY` | No registration needed | [llm7.io ↗](https://llm7.io/) |

You only need **at least one** provider key to get started.

### Model Configuration (`models.yaml`)

Models are defined with ordered fallback chains:

```yaml
models:
  llama-3.3-70b:
    - provider: openrouter
      model: meta-llama/llama-3.3-70b-instruct:free
    - provider: nvidia
      model: meta/llama-3.1-405b-instruct
```

## Usage

### With OpenAI SDK (Python)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="your-master-key",
)

response = client.chat.completions.create(
    model="llama-3.3-70b",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

### With curl

```bash
# List available models
curl http://localhost:8080/v1/models \
  -H "Authorization: Bearer your-master-key"

# Chat completion
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer your-master-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "llama-3.3-70b", "messages": [{"role": "user", "content": "Hello!"}]}'
```

### With any OpenAI-compatible tool

Point any tool that supports custom OpenAI base URLs to `http://localhost:8080/v1` with your master key as the API key. Works with Cursor, LibreChat, Open WebUI, OpenClaw, and more.

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/v1/chat/completions` | POST | Chat completions (with streaming) |
| `/v1/models` | GET | List all available models |
| `/v1/batch` | POST | Batch requests (parallel) |
| `/v1/embeddings` | POST | Text embeddings |
| `/` | GET | Web dashboard |
| `/api/status` | GET | JSON status |
| `/api/analytics` | GET | Usage analytics + savings |
| `/api/keys/validate-all` | POST | Validate all API keys |
| `/api/auto-update` | GET | Re-scan providers for new models |
| `/api/sync-providers` | POST | Sync from awesome-free-llm-apis |
| `/api/config/openclaw` | GET | OpenClaw config export |
| `/api/config/hermes` | GET | Hermes config export |

## Auto-Updates

Keep models fresh with zero effort:

1. **Dashboard** → Setup tab → "Sync Providers" button
2. **Terminal** → `python3 sync_providers.py`
3. **Auto-cron** → weekly sync from awesome-free-llm-apis

New providers and models appear automatically.

## Docker

```bash
docker-compose up -d
```

## Architecture

```
Any AI Tool → Gateway (localhost:8080)
               ├── Auth check (MASTER_KEY)
               ├── Smart routing with 60+ aliases
               ├── Round-robin load balancing
               ├── Rate limit tracking per provider
               ├── Auto-fallback on failure
               ├── Response caching (LRU + TTL)
               ├── Request queuing with backoff
               └── Usage analytics + savings tracker
```

## License

MIT
