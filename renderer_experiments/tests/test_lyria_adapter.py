import os
from app.backends.lyria import LyriaRenderer


def test_lyria_does_not_log_or_require_secret_on_import(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    renderer = LyriaRenderer()
    assert renderer.metrics.audio_chunks == 0


def test_lyria_backend_input_includes_chords_and_energy_prompt(monkeypatch):
    from app.models import MusicIntent
    from app.backends.lyria import LyriaRenderer

    renderer = LyriaRenderer(api_key="test")
    intent = MusicIntent().patch(semantic={"energy": 0.9}, harmony={"chords": ("Am7", "Dm7", "G7", "Cmaj7")})
    payload = renderer.backend_input(intent)
    texts = [entry["text"] for entry in payload["weighted_prompts"]]
    assert "high energy" in texts
    assert "harmonic movement: Am7 - Dm7 - G7 - Cmaj7" in texts
    assert payload["generation_config"]["scale"] == "C_MAJOR_A_MINOR"
