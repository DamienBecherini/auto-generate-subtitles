"""Transcription client against a fake HTTP server: what is requested, and the variants of answers."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from subtitles import segmentation, transcription

WORDS = [{"word": " Bonjour", "start": 0.0, "end": 0.5}, {"word": " à", "start": 0.6, "end": 0.7}]


def fake_server(tmp_path, answer, code=200, raw=None):
    """Returns (url, received requests, server)."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, dict(self.headers), self.rfile.read(int(self.headers["Content-Length"]))))
            self.reply()

        def do_GET(self):
            received.append((self.path, dict(self.headers), b""))
            self.reply()

        def reply(self):
            body = raw if raw is not None else json.dumps(answer).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_port}", received, httpd


@pytest.fixture
def audio(tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(b"RIFFdata")
    return f


def test_timestamped_words_are_requested(tmp_path, audio):
    url, received, httpd = fake_server(tmp_path, {"language": "fr", "words": WORDS, "segments": []})
    try:
        transcription.transcribe(url, audio, "fr", key="k")
    finally:
        httpd.shutdown()
    path, headers, body = received[0]
    assert path == "/v1/audio/transcriptions"
    assert b'name="response_format"\r\n\r\nverbose_json' in body
    assert b'name="timestamp_granularities[]"\r\n\r\nword' in body
    assert b'name="language"\r\n\r\nfr' in body
    assert headers["Authorization"] == "Bearer k"


def test_whisper_answer_usable_by_the_segmentation(tmp_path, audio):
    url, _, httpd = fake_server(tmp_path, {"language": "french", "words": WORDS, "segments": []})
    try:
        r = transcription.transcribe(url, audio)
    finally:
        httpd.shutdown()
    assert [w.text for w in segmentation.from_json(r["words"])] == ["Bonjour", "à"]
    assert r["language"] == ""


@pytest.mark.parametrize("code", ["fr", "pt-BR", "yue"])
def test_language_code_kept(tmp_path, audio, code):
    url, _, httpd = fake_server(tmp_path, {"language": code, "words": WORDS})
    try:
        assert transcription.transcribe(url, audio)["language"] == code
    finally:
        httpd.shutdown()


def test_server_without_words_explains_the_requirement(tmp_path, audio):
    url, _, httpd = fake_server(tmp_path, {"text": "Bonjour", "segments": []})
    try:
        with pytest.raises(transcription.TranscriptionError, match="timestamp_granularities"):
            transcription.transcribe(url, audio)
    finally:
        httpd.shutdown()


def test_announced_health(tmp_path):
    url, _, httpd = fake_server(tmp_path, {"state": "ready"})
    try:
        assert transcription.health(url) == "prêt"          # announced state, in plain words in the language of the messages
    finally:
        httpd.shutdown()


@pytest.mark.parametrize("options", [{"code": 404, "answer": {}}, {"answer": None, "raw": b"OK"}, {"answer": ["ok"]}, {"answer": {"message": "OK"}}])
def test_server_without_health_route_is_not_an_error(tmp_path, options):
    url, _, httpd = fake_server(tmp_path, **options)
    try:
        assert transcription.health(url).startswith("répond")
    finally:
        httpd.shutdown()


def test_unreachable_server_stays_an_error():
    with pytest.raises(transcription.TranscriptionError, match="injoignable"):
        transcription.health("http://127.0.0.1:9", timeout_s=2)
