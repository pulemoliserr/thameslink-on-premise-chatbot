"""Minimal client for a local Ollama server (https://github.com/ollama/ollama/blob/main/docs/api.md)."""
from __future__ import annotations

import requests

from . import config


class OllamaError(RuntimeError):
    pass


def available_models() -> list[str] | None:
    """Model names installed in Ollama, or None if Ollama is not running."""
    try:
        r = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=3)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except (requests.RequestException, ValueError):
        return None


def has_model(name: str, models: list[str] | None = None) -> bool:
    models = available_models() if models is None else models
    if not models:
        return False
    base = name if ":" in name else name + ":latest"
    return name in models or base in models


def embed(texts: list[str], model: str | None = None) -> list[list[float]]:
    model = model or config.EMBED_MODEL
    try:
        r = requests.post(f"{config.OLLAMA_URL}/api/embed",
                          json={"model": model, "input": texts}, timeout=config.OLLAMA_TIMEOUT)
        r.raise_for_status()
        return r.json()["embeddings"]
    except (requests.RequestException, KeyError, ValueError) as e:
        raise OllamaError(f"Embedding with '{model}' failed: {e}") from e


def chat(messages: list[dict], model: str | None = None, temperature: float = 0.0) -> str:
    """Temperature 0 and a fixed seed: the same question with the same passages gives the same answer."""
    model = model or config.CHAT_MODEL
    try:
        r = requests.post(f"{config.OLLAMA_URL}/api/chat",
                          json={"model": model, "messages": messages, "stream": False,
                                "options": {"temperature": temperature, "seed": 42, "num_ctx": 8192}},
                          timeout=config.OLLAMA_TIMEOUT)
        r.raise_for_status()
        return r.json()["message"]["content"]
    except (requests.RequestException, KeyError, ValueError) as e:
        raise OllamaError(f"Chat with '{model}' failed: {e}") from e
