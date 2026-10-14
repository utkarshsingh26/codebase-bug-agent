"""Separate the agent's model interface from a provider's HTTP format."""

from typing import Protocol

from .http import request_json
from .tracing import traced


class Model(Protocol):
    """The agent depends only on this interface, not a model provider."""

    def complete(self, messages: list[dict]) -> str:
        """Accept conversation messages and return the next model text response."""
        ...


class CompatibleModel:
    """Chat Completions adapter for hosted APIs, Ollama, and local servers."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None, *, json_mode=True):
        """Configure the endpoint and model; local servers can omit the API key."""
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.json_mode = json_mode

    @traced("model.complete", "llm")
    def complete(self, messages: list[dict]) -> str:
        """Send a non-streaming chat request and extract its first text response."""
        payload = {"model": self.model, "messages": messages, "stream": False}
        # Ask the server to constrain output, rather than relying only on a prompt.
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        response = request_json(
            self.url,
            # Authentication changes configuration, not the agent's interface.
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
            payload=payload,
        )
        try:
            content = response["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError()
            return content
        except (KeyError, IndexError, TypeError, ValueError):
            raise RuntimeError("Model returned no text in choices[0].message.content.") from None
