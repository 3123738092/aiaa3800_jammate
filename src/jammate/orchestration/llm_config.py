"""Explicit OpenAI configuration. Secrets never belong to the music context."""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class LLMConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    api_key: str = field(repr=False)
    model: str = "gpt-5.4-mini"
    base_url: str = "https://api.openai.com/v1"
    api: str = "responses"
    timeout_s: float = 20.
    max_output_tokens: int = 2400
    reasoning_effort: str | None = None

    def __post_init__(self):
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise LLMConfigurationError("Set OPENAI_API_KEY or provide the private JamMate config")
        if not isinstance(self.model, str) or not self.model.strip():
            raise LLMConfigurationError("model must be a nonempty name")
        endpoint = urlsplit(self.base_url)
        if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise LLMConfigurationError("base_url must be HTTPS without credentials, query or fragment")
        if self.api not in {"responses", "chat_completions"}:
            raise LLMConfigurationError("api must be responses or chat_completions")
        if isinstance(self.timeout_s, bool) or not math.isfinite(self.timeout_s) or not 0 < self.timeout_s <= 60:
            raise LLMConfigurationError("timeout_s must be within (0, 60]")
        if type(self.max_output_tokens) is not int or not 256 <= self.max_output_tokens <= 8192:
            raise LLMConfigurationError("max_output_tokens must be an integer within 256..8192")
        if self.reasoning_effort not in {None, "none", "minimal", "low", "medium", "high", "xhigh"}:
            raise LLMConfigurationError("unsupported reasoning_effort; omit it for model defaults")

    @classmethod
    def load(cls, path=None, *, environ=None):
        env = os.environ if environ is None else environ
        chosen = path or env.get("JAMMATE_LLM_CONFIG")
        config_path = Path(chosen).expanduser() if chosen else Path.home() / ".config" / "jammate" / "openai.json"
        data = {}
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8-sig"))
                if not isinstance(data, dict) or set(data) - set(cls.__dataclass_fields__):
                    raise ValueError()
            except (OSError, ValueError, UnicodeError):
                raise LLMConfigurationError("Invalid private config; expected known LLMConfig fields") from None
        elif chosen:
            raise LLMConfigurationError("Explicit private config file does not exist")
        # An environment key starts a distinct provider configuration. Do not
        # accidentally send it to a different provider left in a private file.
        if "OPENAI_API_KEY" in env:
            data = {"api_key": env["OPENAI_API_KEY"]}
        for name, key in (("OPENAI_BASE_URL", "base_url"), ("JAMMATE_LLM_MODEL", "model"), ("JAMMATE_LLM_API", "api"), ("JAMMATE_LLM_REASONING_EFFORT", "reasoning_effort")):
            if name in env:
                data[key] = env[name]
        data.setdefault("api_key", "")
        try:
            return cls(**data)
        except (TypeError, OverflowError):
            raise LLMConfigurationError("Invalid private config field types") from None

    def public_info(self):
        return {"model": self.model, "api": self.api, "timeout_s": self.timeout_s,
                "max_output_tokens": self.max_output_tokens, "reasoning_effort": self.reasoning_effort}
