# بوابة النماذج اللغوية المجانية 🔑

خادم API متوافق مع OpenAI يجمع **أكثر من 14 مزود نماذج لغوية مجانية** في نقطة وصول واحدة. قم بإعداد مفاتيح API المجانية في ملف `.env`، ثم استخدم **رابط أساسي واحد + مفتاح رئيسي واحد** للوصول إلى جميع النماذج.

**English** | [**العربية**](README_AR.md)

## المميزات

- **نقطة وصول واحدة** — `http://localhost:8080/v1` (متوافق مع OpenAI SDK)
- **أكثر من 14 مزود خدمة** — OpenRouter, GitHub Models, Groq, Cerebras, Cloudflare, HuggingFace, NVIDIA, SiliconFlow, Cohere, Google Gemini, Mistral, Kilo, LLM7, Ollama Cloud
- **أكثر من 260 نموذج مجاني** — اكتشاف تلقائي من جميع المزودين
- **تبديل تلقائي** — إذا فشل مزود (حد المعدل، خطأ، انتهاء المهلة)، ينتقل تلقائياً للتالي
- **توزيع الحمل الدائري** — يوزع الطلبات بين المزودين
- **تتبع حدود المعدل** — مراقبة لكل مزود مع التبديل التلقائي
- **دعم البث** — تمرير SSE الكامل
- **لوحة تحكم ويب** — حالة مباشرة، تحليلات، إدارة المفاتيح على `http://localhost:8080/`
- **مزامنة تلقائية** — يسحب نماذج مجانية جديدة من [awesome-free-llm-apis](https://github.com/mnfst/awesome-free-llm-apis)
- **تحليلات** — تتبع الاستخدام، التوفير المقدر، معدلات نجاح المزودين
- **التحقق من صحة المفاتيح** — اختبار جميع مفاتيح API بنقرة واحدة
- **توجيه ذكي** — أكثر من 60 اسم بديل للنماذج (اكتب "gpt-4" ← أفضل نموذج متاح)
- **طلبات مجمعة** — إرسال عدة طلبات بالتوازي
- **دعم Docker** — أمر واحد للنشر

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

افتح `http://127.0.0.1:8080/` للوصول إلى لوحة التحكم.

## الإعدادات

### متغيرات البيئة (`.env`)

| المتغير | الوصف | الحصول على مفتاح مجاني |
|---|---|---|
| `MASTER_KEY` | مفتاح البوابة الخاص بك | اختر أي شيء تريده |
| `OPENROUTER_KEY` | أكبر كتالوج نماذج مجانية | [openrouter.ai ↗](https://openrouter.ai/keys) |
| `NVIDIA_KEY` | أكثر من 100 نموذج بدون حد يومي | [build.nvidia.com ↗](https://build.nvidia.com/) |
| `GITHUB_KEY` | نماذج OpenAI و Meta و Mistral | [GitHub Tokens ↗](https://github.com/settings/tokens) |
| `GROQ_KEY` | استنتاج فائق السرعة | [console.groq.com ↗](https://console.groq.com/) |
| `CEREBRAS_KEY` | أسرع استنتاج لـ Llama (~2,600 توكن/ثانية) | [cloud.cerebras.ai ↗](https://cloud.cerebras.ai/) |
| `GOOGLE_GEMINI_KEY` | Gemini 2.5 Flash، سياق 1M | [aistudio.google.com ↗](https://aistudio.google.com/apikey) |
| `MISTRAL_KEY` | Mistral Small/Large/Codestral | [console.mistral.ai ↗](https://console.mistral.ai/) |
| `COHERE_KEY` | Command R+ (1000 طلب/شهر) | [dashboard.cohere.com ↗](https://dashboard.cohere.com/) |
| `SILICONFLOW_KEY` | Qwen و DeepSeek و GLM | [siliconflow.cn ↗](https://siliconflow.cn/) |
| `HUGGINGFACE_KEY` | آلاف النماذج المجتمعية | [huggingface.co ↗](https://huggingface.co/settings/tokens) |

تحتاج **مفتاح مزود واحد على الأقل** للبدء.

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

### مع أي أداة متوافقة مع OpenAI

وجّه أي أداة تدعم OpenAI نحو `http://localhost:8080/v1` مع مفتاحك الرئيسي. يعمل مع Cursor، LibreChat، Open WebUI، OpenClaw، والمزيد.

## نقاط API

| النقطة | الطريقة | الوصف |
|---|---|---|
| `/v1/chat/completions` | POST | إكمال المحادثات (مع البث) |
| `/v1/models` | GET | قائمة جميع النماذج المتاحة |
| `/v1/batch` | POST | طلبات مجمعة (بالتوازي) |
| `/v1/embeddings` | POST | تضمين النصوص |
| `/` | GET | لوحة التحكم |
| `/api/status` | GET | حالة JSON |
| `/api/analytics` | GET | تحليلات الاستخدام + التوفير |
| `/api/keys/validate-all` | POST | التحقق من جميع مفاتيح API |
| `/api/auto-update` | GET | إعادة فحص المزودين لنماذج جديدة |
| `/api/sync-providers` | POST | مزامنة من awesome-free-llm-apis |
| `/api/config/openclaw` | GET | تصدير إعدادات OpenClaw |
| `/api/config/hermes` | GET | تصدير إعدادات Hermes |

## التحديثات التلقائية

حافظ على النماذج محدثة بدون جهد:

1. **لوحة التحكم** ← تبويب الإعداد ← زر "مزامنة المزودين"
2. **الطرفية** ← `python3 sync_providers.py`
3. **مهمة مجدولة** ← مزامنة أسبوعية من awesome-free-llm-apis

المزودون والنماذج الجديدة تظهر تلقائياً.

## Docker

```bash
docker-compose up -d
```

## البنية

```
أي أداة ذكاء اصطناعي → البوابة (localhost:8080)
                         ├── فحص المصادقة (MASTER_KEY)
                         ├── توجيه ذكي مع 60+ اسم بديل
                         ├── توزيع حمل دائري
                         ├── تتبع حدود المعدل لكل مزود
                         ├── تبديل تلقائي عند الفشل
                         ├── تخزين مؤقت للاستجابات (LRU + TTL)
                         ├── طوابير طلبات مع إعادة المحاولة
                         └── تحليلات الاستخدام + تتبع التوفير
```

## الترخيص

MIT
