"""
Compatibility shim for emergentintegrations.llm.chat
All providers route through Replit Gemini AI Integration (google-genai SDK).
xAI/Grok removed — _send_xai delegates to _send_gemini.
"""
import os
import asyncio
from typing import Optional, List
from config import GEMINI_AI_ENABLED


class ImageContent:
    def __init__(self, image_base64: str, media_type: str = "image/jpeg"):
        self.image_base64 = image_base64
        self.media_type = media_type


class UserMessage:
    def __init__(self, text: str, file_contents: Optional[List[ImageContent]] = None):
        self.text = text
        self.file_contents = file_contents or []


class LlmChat:
    def __init__(self, api_key: str, session_id: str, system_message: str = ""):
        self.api_key = api_key
        self.session_id = session_id
        self.system_message = system_message
        self._provider = "gemini"
        self._model = "gemini-2.5-flash"
        self._history = []

    def with_model(self, provider: str, model: str):
        self._provider = provider.lower()
        self._model = model
        return self

    async def send_message(self, message: UserMessage) -> str:
        if not GEMINI_AI_ENABLED:
            return ""
        return await self._send_gemini(message)

    async def _send_gemini(self, message: UserMessage) -> str:
        try:
            from google import genai as _genai
            from google.genai import types as _gtypes
            _key = os.environ.get("AI_INTEGRATIONS_GEMINI_API_KEY", self.api_key)
            _base = os.environ.get("AI_INTEGRATIONS_GEMINI_BASE_URL", "").rstrip("/")
            _client = _genai.Client(
                api_key=_key,
                http_options={"api_version": "", "base_url": _base} if _base else {},
            )

            # Build multi-turn contents from history
            contents = []
            for h in self._history:
                role = h.get("role", "user")
                parts_raw = h.get("parts", [h.get("content", "")])
                text = parts_raw[0] if parts_raw else ""
                contents.append({"role": role, "parts": [{"text": str(text)}]})

            # Current message (text + optional images)
            current_parts = []
            for img in message.file_contents:
                import base64
                img_bytes = base64.b64decode(img.image_base64) if isinstance(img.image_base64, str) else img.image_base64
                current_parts.append({
                    "inline_data": {"mime_type": img.media_type, "data": img_bytes}
                })
            current_parts.append({"text": message.text})
            contents.append({"role": "user", "parts": current_parts})

            cfg = _gtypes.GenerateContentConfig(
                max_output_tokens=8192,
                thinking_config=_gtypes.ThinkingConfig(thinking_budget=0),
            )
            if self.system_message:
                cfg.system_instruction = self.system_message

            resp = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _client.models.generate_content(
                    model=self._model, contents=contents, config=cfg,
                ),
            )
            result = (resp.text or "").strip()
            self._history.append({"role": "user", "parts": [message.text]})
            self._history.append({"role": "model", "parts": [result]})
            return result
        except Exception as e:
            return f"[LLM Error: {e}]"

    # xAI removed — route through Gemini
    async def _send_xai(self, message: UserMessage) -> str:
        return await self._send_gemini(message)

    async def _send_openai(self, message: UserMessage) -> str:
        return await self._send_gemini(message)
