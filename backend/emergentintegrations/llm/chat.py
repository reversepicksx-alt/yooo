"""
Compatibility shim for emergentintegrations.llm.chat
Implements LlmChat, UserMessage, ImageContent using openai and google-genai SDKs.
"""
import os
import asyncio
from typing import Optional, List


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
        self._model = "gemini-2.0-flash"
        self._history = []

    def with_model(self, provider: str, model: str):
        self._provider = provider.lower()
        self._model = model
        return self

    async def send_message(self, message: UserMessage) -> str:
        if self._provider == "gemini":
            return await self._send_gemini(message)
        elif self._provider in ("openai", "gpt"):
            return await self._send_openai(message)
        elif self._provider in ("xai", "grok"):
            return await self._send_xai(message)
        else:
            return await self._send_gemini(message)

    async def _send_gemini(self, message: UserMessage) -> str:
        try:
            import google.generativeai as genai
            genai.configure(api_key=self.api_key)
            model = genai.GenerativeModel(
                model_name=self._model,
                system_instruction=self.system_message if self.system_message else None
            )
            parts = []
            for img in message.file_contents:
                import base64
                parts.append({
                    "inline_data": {
                        "mime_type": img.media_type,
                        "data": img.image_base64
                    }
                })
            parts.append(message.text)

            history_for_gemini = []
            for h in self._history:
                history_for_gemini.append(h)

            chat = model.start_chat(history=history_for_gemini)
            loop = asyncio.get_event_loop()
            if len(parts) == 1:
                resp = await loop.run_in_executor(None, lambda: chat.send_message(parts[0]))
            else:
                resp = await loop.run_in_executor(None, lambda: chat.send_message(parts))

            result = resp.text
            self._history.append({"role": "user", "parts": [message.text]})
            self._history.append({"role": "model", "parts": [result]})
            return result
        except Exception as e:
            return f"[LLM Error: {e}]"

    async def _send_openai(self, message: UserMessage) -> str:
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=self.api_key)
            messages = []
            if self.system_message:
                messages.append({"role": "system", "content": self.system_message})
            for h in self._history:
                messages.append(h)

            content = []
            content.append({"type": "text", "text": message.text})
            for img in message.file_contents:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{img.media_type};base64,{img.image_base64}"}
                })

            if len(content) == 1:
                messages.append({"role": "user", "content": message.text})
            else:
                messages.append({"role": "user", "content": content})

            resp = await client.chat.completions.create(
                model=self._model,
                messages=messages
            )
            result = resp.choices[0].message.content
            self._history.append({"role": "user", "content": message.text})
            self._history.append({"role": "assistant", "content": result})
            return result
        except Exception as e:
            return f"[LLM Error: {e}]"

    async def _send_xai(self, message: UserMessage) -> str:
        try:
            from openai import AsyncOpenAI
            xai_key = os.environ.get("XAI_API_KEY", self.api_key)
            client = AsyncOpenAI(api_key=xai_key, base_url="https://api.x.ai/v1")
            messages = []
            if self.system_message:
                messages.append({"role": "system", "content": self.system_message})
            for h in self._history:
                messages.append(h)

            content = []
            content.append({"type": "text", "text": message.text})
            for img in message.file_contents:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{img.media_type};base64,{img.image_base64}"}
                })

            if len(content) == 1:
                messages.append({"role": "user", "content": message.text})
            else:
                messages.append({"role": "user", "content": content})

            resp = await client.chat.completions.create(
                model=self._model,
                messages=messages
            )
            result = resp.choices[0].message.content
            self._history.append({"role": "user", "content": message.text})
            self._history.append({"role": "assistant", "content": result})
            return result
        except Exception as e:
            return f"[LLM Error: {e}]"
