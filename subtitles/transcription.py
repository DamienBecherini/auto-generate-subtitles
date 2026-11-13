"""Client of the transcription server (OpenAI-compatible API, POST /v1/audio/transcriptions, verbose_json).

The server must return the timestamped words on the timeline of the recording:
{"language", "duration", "text", "segments": [...], "words": [{"word", "start", "end"}]}. They are requested with
`timestamp_granularities[]=word`, as in the OpenAI API.
"""
import http.client
import json
import re
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from . import i18n


_LANGUAGE_CODE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")
CANCEL_CHECK_S = 0.5


class TranscriptionError(Exception):
    pass


class TranscriptionTimeout(TranscriptionError):
    """The server did not answer in time (or its answer got lost on the way)."""


class TranscriptionCancelled(TranscriptionError):
    """Stopped on request: the connection was closed, which tells the server to stop too (transcription-handy-like
    stops between two chunks; another server finishes its work for nothing)."""


def health_state(d) -> str:
    """State announced by a /health answer of transcription-handy-like ("ready", "loading"), "" if there is none."""
    if not isinstance(d, dict):
        return ""
    return str(d.get("state") or "")


def _post(server_url: str, path: str, body: bytes, headers: dict, timeout_s: float, cancelled) -> tuple[int, bytes]:
    """POST that can be interrupted: the exchange runs in a thread; when `cancelled()` becomes true, the socket is
    shut down (the server sees the client leave) and TranscriptionCancelled is raised. Returns (status, body)."""
    u = urllib.parse.urlsplit(server_url.rstrip("/"))
    connection_class = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = connection_class(u.hostname, u.port, timeout=timeout_s)
    outcome: dict = {}

    def exchange():
        try:
            conn.request("POST", (u.path or "") + path, body, headers)
            resp = conn.getresponse()
            outcome["answer"] = (resp.status, resp.read())
        except BaseException as e:  # noqa: BLE001 - handed to the waiting thread
            outcome["error"] = e

    worker = threading.Thread(target=exchange, daemon=True, name="transcription-request")
    worker.start()
    while worker.is_alive():
        worker.join(CANCEL_CHECK_S)
        if worker.is_alive() and cancelled():
            try:
                if conn.sock:
                    conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
            raise TranscriptionCancelled(i18n.t("err.cancelled"))
    conn.close()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["answer"]


def transcribe(server_url: str, audio: Path, language: str | None = None, timeout_s: int = 7200, key: str = "",
               cancelled=lambda: False) -> dict:
    """Timestamped words of the recording. `cancelled()`: checked twice a second; true closes the connection."""
    sep = uuid.uuid4().hex
    fields = [("response_format", "verbose_json"), ("timestamp_granularities[]", "word")]
    if language:
        fields.append(("language", language))
    parts = [f'--{sep}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{val}\r\n'.encode() for name, val in fields]
    parts.append((f'--{sep}\r\nContent-Disposition: form-data; name="file"; filename="{audio.name}"\r\n'
                  f"Content-Type: application/octet-stream\r\n\r\n").encode())
    body = b"".join([*parts, audio.read_bytes(), f"\r\n--{sep}--\r\n".encode()])   # a single copy of the audio
    headers = {"Content-Type": f"multipart/form-data; boundary={sep}"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        status, raw = _post(server_url, "/v1/audio/transcriptions", body, headers, timeout_s, cancelled)
    except TranscriptionError:
        raise
    except (TimeoutError, socket.timeout) as e:
        raise TranscriptionTimeout(i18n.t("err.server_timeout", s=timeout_s)) from e
    except (OSError, http.client.HTTPException) as e:
        raise TranscriptionError(i18n.t("err.server_unreachable", url=server_url, detail=e)) from e
    if status != 200:
        raise TranscriptionError(i18n.t("err.server_status", code=status, detail=raw.decode("utf-8", errors="replace")[:500]))
    try:
        r = json.loads(raw)
    except ValueError as e:
        raise TranscriptionError(i18n.t("err.server_status", code=status, detail=raw.decode("utf-8", errors="replace")[:200])) from e
    if not isinstance(r, dict) or "words" not in r:
        raise TranscriptionError(i18n.t("err.no_words"))
    if not _LANGUAGE_CODE.match(str(r.get("language") or "")):
        r["language"] = ""             # OpenAI returns a name ("french"): the language is then guessed from the text
    return r


def health(server_url: str, timeout_s: int = 5) -> str:
    """State announced by /health. This route is not in the OpenAI API: only an unreachable server is an error."""
    try:
        with urllib.request.urlopen(server_url.rstrip("/") + "/health", timeout=timeout_s) as resp:
            d = json.load(resp)
    except urllib.error.HTTPError:
        return i18n.t("state.responds_no_health")
    except ValueError:
        return i18n.t("state.responds")
    except (urllib.error.URLError, OSError) as e:
        raise TranscriptionError(i18n.t("err.transcription_unreachable", url=server_url, detail=e)) from e
    state = health_state(d)
    return i18n.label("state.server.", state) if state else i18n.t("state.responds")
