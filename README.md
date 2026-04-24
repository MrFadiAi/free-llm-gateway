# Free LLM Gateway 🔑

A unified OpenAI-compatible API server that aggregates **14 free LLM providers** into one endpoint. Configure your free API keys in `.env`, then use **one base URL + one master key** to access every model.

## Features

- **Single endpoint** — `http://localhost:8080/v1` (OpenAI SDK-compatible)
- **14 providers** — OpenRouter, GitHub Models, Groq, Cerebras, Cloudflare, HuggingFace, NVIDIA, SiliconFlow, Cohere, Google Gemini, Mistral, Kilo, LLM7, Ollama Cloud
- **40+ models** — pre-configured with provider fallback chains
- **Automatic fallback** — if one provider fails (rate limit, error, timeout), tries the next
- **Rate limit tracking** — per-provider RPM/RPD monitoring with auto-rotation
- **Streaming support** — full SSE passthrough
- **Web dashboard** — live status at `http://localhost:8080/`

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

## Configuration

### Docker

Run with Docker Compose:

```bash
# 1. Configure API keys
cp .env.example .env
# Edit .env — add at least one provider API key

# 2. Build and start
docker compose up -d

# 3. Check status
docker compose ps
curl http://localhost:8080/api/status

# View logs
docker compose logs -f

# Stop
docker compose down
```

The `data/` directory is mounted as a volume for persistent usage data. The `.env` file is mounted read-only.

### Environment Variables (`.env`)

| Variable | Description |
|---|---|
| `MASTER_KEY` | Your gateway API key — clients must send `Authorization: Bearer <MASTER_KEY>` |
| `OPENROUTER_KEY` | [Get free key ↗](https://openrouter.ai/) — largest free model catalog |
| `GITHUB_KEY` | [Get free key ↗](https://github.com/marketplace/models) — OpenAI, Meta, Mistral models |
| `GROQ_KEY` | [Get free key ↗](https://console.groq.com/) — ultra-fast inference |
| `CEREBRAS_KEY` | [Get free key ↗](https://cloud.cerebras.ai/) — fastest Llama inference |
| `NVIDIA_KEY` | [Get free key ↗](https://build.nvidia.com/) — 100+ models, no daily cap |
| `GOOGLE_GEMINI_KEY` | [Get free key ↗](https://aistudio.google.com/apikey) — Gemini 2.5 Flash |
| `MISTRAL_KEY` | [Get free key ↗](https://console.mistral.ai/) — Mistral Small/Large/Codestral |
| `COHERE_KEY` | [Get free key ↗](https://dashboard.cohere.com/) — Command R+ |
| `SILICONFLOW_KEY` | [Get free key ↗](https://siliconflow.cn/) — Qwen, DeepSeek, GLM |
| `HUGGINGFACE_KEY` | [Get free key ↗](https://huggingface.co/settings/tokens) |
| `CLOUDFLARE_KEY` | Cloudflare Workers AI key |
| `KILO_KEY` | Kilo API key |
| `LLM7_KEY` | LLM7 API key (no key needed for basic access) |
| `HOST` | Server host (default: `0.0.0.0`) |
| `PORT` | Server port (default: `8080`) |
| `RETRY_MAX_ATTEMPTS` | Max retries per provider on 5xx errors (default: `2`) |
| `RETRY_BACKOFF_BASE` | Base seconds for exponential backoff (default: `1.0`, sequence: 1s, 2s, 4s...) |

You only need **at least one** provider key to get started. Add more for better availability.

**Multiple keys per provider** — load balance across keys:
```bash
# Comma-separated
OPENROUTER_KEY=key1,key2,key3

# Or indexed
OPENROUTER_KEY_1=key1
OPENROUTER_KEY_2=key2
```
Keys are rotated round-robin. On 429/auth errors, the gateway automatically switches to the next key.

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

The gateway tries providers in order. If the first fails, it falls back to the next.

## Usage

### With OpenAI SDK (Python)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="your-master-key",
)

# Chat completion
response = client.chat.completions.create(
    model="llama-3.3-70b",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)

# Streaming
stream = client.chat.completions.create(
    model="deepseek-r1",
    messages=[{"role": "user", "content": "Explain quantum computing"}],
    stream=True,
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
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

# Streaming
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer your-master-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "deepseek-r1", "messages": [{"role": "user", "content": "Hello!"}], "stream": true}'
```

### With any OpenAI-compatible tool

Point any tool that supports custom OpenAI base URLs to `http://localhost:8080/v1` with your master key as the API key. Works with Cursor, LibreChat, Open WebUI, and more.

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/v1/chat/completions` | POST | Chat completions (with streaming) |
| `/v1/models` | GET | List all available models |
| `/v1/embeddings` | POST | Text embeddings (pass-through) |
| `/` | GET | Web dashboard |
| `/api/status` | GET | JSON status (models, rate limits, logs) |

## Architecture

```
Client → Gateway (localhost:8080)
           ├── Auth check (MASTER_KEY)
           ├── Route to provider fallback chain
           ├── Rate limit check per provider
           ├── Forward request to provider
           └── Fallback on failure/rate-limit
```

## License

MIT

---

# بوابة النماذج اللغوية المجانية 🔑

خادم API متوافق مع OpenAI يجمع **14 مزود نماذج لغوية مجانية** في نقطة وصول واحدة. قم بإعداد مفاتيح API المجانية في ملف `.env`، ثم استخدم **رابط أساسي واحد + مفتاح رئيسي واحد** للوصول إلى جميع النماذج.

## المميزات

- **نقطة وصول واحدة** — `http://localhost:8080/v1` (متوافق مع OpenAI SDK)
- **14 مزود خدمة** — OpenRouter, GitHub Models, Groq, Cerebras, Cloudflare, HuggingFace, NVIDIA, SiliconFlow, Cohere, Google Gemini, Mistral, Kilo, LLM7, Ollama Cloud
- **أكثر من 40 نموذج** — مُعدة مسبقاً بسلاسل احتياطية بين المزودين
- **تبديل تلقائي** — إذا فشل مزود (حد المعدل، خطأ، انتهاء المهلة)، ينتقل تلقائياً للتالي
- **تتبع حدود المعدل** — مراقبة RPM/RPD لكل مزود مع التبديل التلقائي
- **دعم البث** — تمرير SSE الكامل
- **لوحة تحكم ويب** — حالة مباشرة على `http://localhost:8080/`

## التشغيل السريع

```bash
# 1. استنساخ المشروع
git clone https://github.com/MrFadiAi/free-llm-gateway.git
cd free-llm-gateway

# 2. تثبيت المتطلبات
pip install -r requirements.txt

# 3. إعداد مفاتيح API
cp .env.example .env
# عدّل .env — أضف مفتاح مزود واحد على الأقل

# 4. تشغيل البوابة
python main.py
```

### تشغيل مع Docker

```bash
# 1. إعداد مفاتيح API
cp .env.example .env
# عدّل .env — أضف مفتاح مزود واحد على الأقل

# 2. بناء وتشغيل
docker compose up -d

# 3. التحقق من الحالة
docker compose ps
curl http://localhost:8080/api/status

# عرض السجلات
docker compose logs -f

# إيقاف
docker compose down
```

مجلد `data/` مُثبَّت كحجم للبيانات المستمرة. ملف `.env` مُثبَّت للقراءة فقط.

## الإعدادات

### متغيرات البيئة (`.env`)

| المتغير | الوصف |
|---|---|
| `MASTER_KEY` | مفتاح البوابة الخاص بك — يجب على العملاء إرسال `Authorization: Bearer <MASTER_KEY>` |
| `OPENROUTER_KEY` | [احصل على مفتاح مجاني ↗](https://openrouter.ai/) — أكبر كتالوج نماذج مجانية |
| `GITHUB_KEY` | [احصل على مفتاح مجاني ↗](https://github.com/marketplace/models) — نماذج OpenAI و Meta و Mistral |
| `GROQ_KEY` | [احصل على مفتاح مجاني ↗](https://console.groq.com/) — استنتاج فائق السرعة |
| `CEREBRAS_KEY` | [احصل على مفتاح مجاني ↗](https://cloud.cerebras.ai/) — أسرع استنتاج لـ Llama |
| `NVIDIA_KEY` | [احصل على مفتاح مجاني ↗](https://build.nvidia.com/) — أكثر من 100 نموذج بدون حد يومي |
| `GOOGLE_GEMINI_KEY` | [احصل على مفتاح مجاني ↗](https://aistudio.google.com/apikey) — Gemini 2.5 Flash |
| `MISTRAL_KEY` | [احصل على مفتاح مجاني ↗](https://console.mistral.ai/) — Mistral Small/Large/Codestral |
| `COHERE_KEY` | [احصل على مفتاح مجاني ↗](https://dashboard.cohere.com/) — Command R+ |
| `SILICONFLOW_KEY` | [احصل على مفتاح مجاني ↗](https://siliconflow.cn/) — Qwen و DeepSeek و GLM |

تحتاج **مفتاح مزود واحد على الأقل** للبدء. أضف المزيد لزيادة التوفر.

**مفاتيح متعددة لكل مزود** — توزيع الحمل عبر المفاتيح:
```bash
# مفصولة بفواصل
OPENROUTER_KEY=key1,key2,key3

# أو مفهرسة
OPENROUTER_KEY_1=key1
OPENROUTER_KEY_2=key2
```
يتم تدوير المفاتيح بالتناوب. عند خطأ 429/مصادقة، تنتقل البوابة تلقائياً إلى المفتاح التالي.

### إعداد النماذج (`models.yaml`)

النماذج مُعرَّفة بسلاسل احتياطية مرتبة:

```yaml
models:
  llama-3.3-70b:
    - provider: openrouter
      model: meta-llama/llama-3.3-70b-instruct:free
    - provider: nvidia
      model: meta/llama-3.1-405b-instruct
```

تحاول البوابة المزودين بالترتيب. إذا فشل الأول، تنتقل إلى التالي.

## الاستخدام

### مع OpenAI SDK (بايثون)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="your-master-key",
)

response = client.chat.completions.create(
    model="llama-3.3-70b",
    messages=[{"role": "user", "content": "مرحبا!"}],
)
print(response.choices[0].message.content)
```

### مع curl

```bash
# قائمة النماذج المتاحة
curl http://localhost:8080/v1/models \
  -H "Authorization: Bearer your-master-key"

# إكمال محادثة
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer your-master-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "llama-3.3-70b", "messages": [{"role": "user", "content": "مرحبا!"}]}'
```

## نقاط API

| النقطة | الطريقة | الوصف |
|---|---|---|
| `/v1/chat/completions` | POST | إكمال المحادثات (مع البث) |
| `/v1/models` | GET | قائمة جميع النماذج المتاحة |
| `/v1/embeddings` | POST | تضمين النصوص |
| `/` | GET | لوحة التحكم |
| `/api/status` | GET | حالة JSON (نماذج، حدود، سجلات) |

## الترخيص

MIT
