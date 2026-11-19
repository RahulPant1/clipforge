"""Thin Ollama client for Gemma with schema-enforced JSON output."""
import json
from typing import Any, Protocol

import requests

from ..config import settings


class LLM(Protocol):
    def chat_json(self, prompt: str, schema: dict, temperature: float | None = None,
                  think: bool | None = None) -> dict: ...
    # think: None = model default (not sent); False/True = Ollama reasoning off/on (True fails on non-thinking models)
    def unload(self) -> None: ...


def _raise_for_ollama_error(r: requests.Response) -> None:
    """Like raise_for_status, but keeps Ollama's own message (e.g. 'model does not support thinking')."""
    if r.status_code < 400:
        return
    try:
        detail = r.json().get("error", "")
    except ValueError:
        detail = r.text[:200]
    raise RuntimeError(f"Ollama {r.status_code}: {detail or r.reason}")


class OllamaClient:
    def __init__(self, url: str | None = None, model: str | None = None):
        self.url = (url or settings.ollama_url).rstrip("/")
        self.model = model or settings.gemma_model

    def chat_json(self, prompt: str, schema: dict, temperature: float | None = None,
                  think: bool | None = None) -> dict:
        body: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "format": schema,  # Ollama structured outputs
            "messages": [{"role": "user", "content": prompt}],
            "options": {
                "num_ctx": settings.ollama_num_ctx,
                "temperature": settings.ollama_temperature if temperature is None else temperature,
            },
        }
        if think is not None:
            body["think"] = think
        r = requests.post(f"{self.url}/api/chat", json=body, timeout=settings.ollama_timeout)
        _raise_for_ollama_error(r)
        data = r.json()
        if data.get("done_reason") == "length":  # thinking + answer hit num_ctx: content is cut off or empty
            raise RuntimeError(f"Gemma ran out of context (num_ctx={settings.ollama_num_ctx}) before finishing "
                               f"its answer — raise OLLAMA_NUM_CTX or lower WINDOW_SECONDS")
        return json.loads(data["message"]["content"])

    def unload(self) -> None:
        """Ask Ollama to drop the model from VRAM immediately."""
        try:
            requests.post(f"{self.url}/api/generate",
                          json={"model": self.model, "keep_alive": 0}, timeout=30)
        except requests.RequestException:
            pass

    def health(self) -> dict[str, Any]:
        r = requests.get(f"{self.url}/api/tags", timeout=5)
        r.raise_for_status()
        names = [m["name"] for m in r.json().get("models", [])]
        return {"ok": True, "model": self.model, "model_available": self.model in names, "models": names}
