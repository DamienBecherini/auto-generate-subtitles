from subtitles import estimation, pipeline

CFG = {
    "translation": {"chunk_size": 25},
    "translators": [
        {"id": "local", "type": "openai", "name": "IA locale", "url": "http://127.0.0.1:8080"},
        {"id": "claude", "type": "claude", "name": "Claude", "model": "claude-sonnet-5-5"},
        {"id": "opus", "type": "claude", "name": "Opus daté", "model": "claude-opus-5-5-20261001"},
        {"id": "unknown", "type": "claude", "name": "?", "model": "claude-futur-9"},
        {"id": "remote", "type": "openai", "name": "Mistral", "url": "https://api.mistral.ai"},
        {"id": "none", "type": "none", "name": "Aucun"},
    ],
}


def test_estimate_one_language():
    e = estimation.estimate(CFG, 499, ["fr"], "en")
    assert e["languages"] == ["fr"] and e["requests"] == 20
    assert e["input_tokens"] == 499 * 36 and e["output_tokens"] == 499 * 23
    by_id = {t["id"]: t for t in e["translators"]}
    assert abs(by_id["claude"]["cost_usd"] - (499 * 36 * 2 + 499 * 23 * 10) / 1e6) < 1e-4   # ≈ $0.15 (rounded to 4 decimals)
    assert by_id["opus"]["cost_usd"] > by_id["claude"]["cost_usd"]                           # model prefix recognised
    assert by_id["unknown"]["cost_usd"] is None
    assert by_id["local"]["cost_usd"] == 0.0 and by_id["local"]["duration_s"] == round(499 * 0.6)
    assert by_id["remote"]["cost_usd"] is None
    assert by_id["none"]["cost_usd"] == 0.0


def test_source_language_skipped_and_several_languages():
    e = estimation.estimate(CFG, 100, ["en", "fr", "de"], "en")
    assert e["languages"] == ["fr", "de"] and e["requests"] == 8 and e["input_tokens"] == 200 * 36
    assert estimation.estimate(CFG, 100, ["en"], "en")["requests"] == 0


def test_transcription_message():
    assert "environ 4 min 10 s pour 30 min 00 s d'audio (« Serveur », 7.2 s" in pipeline.transcription_message({"name": "Serveur", "speed": 7.2}, 1800)
    assert "environ 3 min 00 s" in pipeline.transcription_message({"name": "PC", "speed": "10"}, 1800)   # string typed in Settings
    for m in ({"name": "X"}, {"name": "X", "speed": "?"}, {"name": "X", "speed": 0}):
        assert "non renseignée" in pipeline.transcription_message(m, 1800)
    assert pipeline.duration_text(3725) == "1 h 02 min" and pipeline.duration_text(42) == "42 s"


def test_transcription_timeout():
    assert pipeline.transcription_timeout({"speed": 7.2}, 1800) == 1000
    assert pipeline.transcription_timeout({"speed": 16}, 60) == 300       # floor
    assert pipeline.transcription_timeout({}, 1800) == 7200


def test_no_answer_from_the_server_raises_timeout(tmp_path):
    import socket

    import pytest

    from subtitles import transcription
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"x")
    with socket.socket() as s:                  # accepts the connection and never answers
        s.bind(("127.0.0.1", 0))
        s.listen()
        with pytest.raises(transcription.TranscriptionTimeout):
            transcription.transcribe(f"http://127.0.0.1:{s.getsockname()[1]}", audio, timeout_s=1)


def test_pause_by_default():
    assert not estimation.pause_by_default({"type": "openai", "url": "http://127.0.0.1:8080"})
    assert not estimation.pause_by_default({"type": "none"})
    assert estimation.pause_by_default({"type": "claude"})
    assert estimation.pause_by_default({"type": "openai", "url": "https://api.openai.com"})


def test_prices_overridden_by_the_config():
    cfg = {**CFG, "estimation": {"prices": {"claude-sonnet-5-5": [1.0, 5.0]}, "input_tokens_per_line": 10, "output_tokens_per_line": 10}}
    e = estimation.estimate(cfg, 1000, ["fr"], "en")
    assert e["input_tokens"] == 10000
    assert {t["id"]: t for t in e["translators"]}["claude"]["cost_usd"] == 0.06
    assert estimation.model_price(cfg, "claude-haiku-5-5") == (0.10, 0.50)      # the defaults stay


def test_text():
    t = estimation.text(estimation.estimate(CFG, 499, ["fr"], "en"), CFG)
    assert "20 requêtes" in t and "Claude (claude-sonnet-5-5) : ≈ 0.15 $" in t and "IA locale : ≈ 4 min 59 s (gratuit)" in t
    assert "Rien à traduire" in estimation.text(estimation.estimate(CFG, 10, ["en"], "en"), CFG)
