from __future__ import annotations

import inspect
import os
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ..models import MusicIntent


@dataclass
class RendererMetrics:
    connected_at: float | None = None
    play_requested_at: float | None = None
    first_audio_at: float | None = None
    update_events: list[dict[str, float | str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    audio_chunks: int = 0
    audio_bytes: int = 0

    def as_dict(self) -> dict[str, Any]:
        data = {
            "connected_at": self.connected_at,
            "first_audio_at": self.first_audio_at,
            "audio_chunks": self.audio_chunks,
            "audio_bytes": self.audio_bytes,
            "update_events": self.update_events,
            "errors": self.errors,
        }
        if self.play_requested_at and self.first_audio_at:
            data["ttfa_ms"] = round((self.first_audio_at - self.play_requested_at) * 1000, 3)
        return data


class LyriaRenderer:
    def __init__(self, api_key: str | None = None, model: str = "models/lyria-realtime-exp"):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = model
        self.session: Any = None
        self._connection: Any = None
        self.metrics = RendererMetrics()
        self.intent: MusicIntent | None = None

    async def connect(self) -> None:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("google-genai is required for Lyria tests") from exc
        client = genai.Client(api_key=self.api_key)
        self._connection = client.aio.live.music.connect(model=self.model)
        self.session = await self._connection.__aenter__()
        self.metrics.connected_at = time.monotonic()

    def backend_input(self, intent: MusicIntent) -> dict[str, Any]:
        from google.genai import types

        scale_name = {"C": "C_MAJOR_A_MINOR", "D": "D_MAJOR_B_MINOR", "E": "E_MAJOR_D_FLAT_MINOR", "F": "F_MAJOR_D_MINOR", "G": "G_MAJOR_E_MINOR", "A": "A_MAJOR_G_FLAT_MINOR", "B": "B_MAJOR_A_FLAT_MINOR"}.get(intent.harmony.key, "C_MAJOR_A_MINOR")
        energy_word = "very low energy" if intent.semantic.energy < .35 else "high energy" if intent.semantic.energy > .7 else "moderate energy"
        prompt_items = [
            {"text": ", ".join(intent.semantic.style), "weight": 1.0},
            {"text": ", ".join(intent.semantic.mood), "weight": 0.65},
            {"text": intent.prompt, "weight": 1.0},
            {"text": energy_word, "weight": 0.75},
            {"text": f"harmonic movement: {' - '.join(intent.harmony.chords)}", "weight": 0.55},
        ]
        return {
            "weighted_prompts": prompt_items,
            "generation_config": {
                "bpm": int(intent.tempo.bpm), "density": intent.semantic.density,
                "brightness": intent.semantic.brightness, "scale": scale_name,
                "mute_bass": intent.parts.bass <= 0, "mute_drums": intent.parts.drums <= 0,
            },
        }

    async def _send_config(self, intent: MusicIntent) -> None:
        from google.genai import types

        payload = self.backend_input(intent)
        await self._call("set_weighted_prompts", prompts=[types.WeightedPrompt(**item) for item in payload["weighted_prompts"]])
        config = payload["generation_config"]
        await self._call(
            "set_music_generation_config",
            config=types.LiveMusicGenerationConfig(
                bpm=config["bpm"], density=config["density"], brightness=config["brightness"],
                mute_drums=config["mute_drums"], mute_bass=config["mute_bass"],
                scale=getattr(types.Scale, config["scale"]),
            ),
        )

    async def start(self, intent: MusicIntent) -> None:
        if self.session is None:
            raise RuntimeError("connect() must be called first")
        self.intent = intent
        await self._send_config(intent)
        self.metrics.play_requested_at = time.monotonic()
        await self._call("play")

    async def _call(self, name: str, **kwargs: Any) -> Any:
        method = getattr(self.session, name, None)
        if method is None:
            raise RuntimeError(f"Lyria session does not support {name}")
        result = method(**kwargs)
        return await result if inspect.isawaitable(result) else result

    async def receive(self, duration: float = 30.0) -> AsyncIterator[bytes]:
        if self.session is None:
            raise RuntimeError("connect() must be called first")
        deadline = time.monotonic() + duration
        try:
            async for message in self.session.receive():
                if time.monotonic() >= deadline:
                    break
                content = getattr(message, "server_content", None)
                for chunk in getattr(content, "audio_chunks", None) or []:
                    if self.metrics.first_audio_at is None:
                        self.metrics.first_audio_at = time.monotonic()
                    self.metrics.audio_chunks += 1
                    data = chunk.data
                    self.metrics.audio_bytes += len(data)
                    yield bytes(data)
        except Exception as exc:
            self.metrics.errors.append({"type": type(exc).__name__, "message": str(exc)})
            raise

    async def update(self, intent: MusicIntent) -> None:
        if self.session is None:
            raise RuntimeError("connect() must be called first")
        sent = time.monotonic()
        await self._send_config(intent)
        if self.intent and (intent.tempo != self.intent.tempo or intent.harmony != self.intent.harmony):
            await self._call("reset_context")
        self.intent = intent
        self.metrics.update_events.append({"sent_at": sent, "completed_at": time.monotonic(), "kind": "intent_update"})

    async def pause(self) -> None: await self._call("pause")

    async def resume(self) -> None: await self._call("play")

    async def reset(self) -> None: await self._call("reset_context")

    async def stop(self) -> None:
        if self.session is not None:
            await self._call("stop")
            await self._connection.__aexit__(None, None, None)
            self.session = None
            self._connection = None

