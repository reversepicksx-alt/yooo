---
name: Replit Gemini AI Integration proxy quirk
description: How to correctly initialize the google-genai SDK to work with Replit's AI Integration proxy
---

## Rule
When using the Replit AI Integration Gemini proxy (`AI_INTEGRATIONS_GEMINI_BASE_URL` = `http://localhost:1106/modelfarm/gemini`), you MUST set `api_version: ''` (empty string) in the google-genai SDK's `http_options`. Without it, the SDK appends `/v1beta/` to all paths and the proxy returns `INVALID_ENDPOINT`.

```python
client = genai.Client(
    api_key=os.environ["AI_INTEGRATIONS_GEMINI_API_KEY"],
    http_options={
        "api_version": "",   # ← required — strips /v1beta/ prefix
        "base_url": os.environ["AI_INTEGRATIONS_GEMINI_BASE_URL"],
    },
)
```

**Why:** The Replit proxy at `localhost:1106/modelfarm/gemini` does NOT implement the standard Gemini REST API endpoint format. It expects paths like `/models/{model}:generateContent` (no version prefix). The google-genai v2 SDK defaults to `/v1beta/` which the proxy rejects with `{"error": {"code": "INVALID_ENDPOINT"}}`.

**How to apply:** Any new Gemini client instantiation in this project must use this exact pattern. The `api_version: ''` trick is documented in the blueprint's `gemini.py` code snippet.

## Also: Python 3.12 path
All Python packages (uvicorn, google-genai, etc.) live in `.pythonlibs/lib/python3.12/`. The system `python`/`python3` resolves to 3.11 (missing packages). Always invoke:
`/home/runner/workspace/.pythonlibs/bin/python3.12`
in workflow commands and scripts.
