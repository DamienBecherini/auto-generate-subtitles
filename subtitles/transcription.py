"""Client of the transcription server (OpenAI-compatible API, POST /v1/audio/transcriptions, verbose_json).

The server must return the timestamped words on the timeline of the recording:
{"language", "duration", "text", "segments": [...], "words": [{"word", "start", "end"}]}. They are requested with
`timestamp_granularities[]=word`, as in the OpenAI API.
"""
import json
import re
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import i18n


_LANGUAGE_CODE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")


class TranscriptionError(Exception):
    pass


class TranscriptionTimeout(TranscriptionError):
    """The server did not answer in time (or its answer got lost on the way)."""


def health_state(d) -> str:
    """State announced by a /health answer of transcription-handy-like ("ready", "loading"), "" if there is none."""
    if not isinstance(d, dict):
        return ""
    return str(d.get("state") or "")


def transcribe(server_url: str, audio: Path, language: str | None = None, timeout_s: int = 7200, key: str = "") -> dict:
    data = audio.read_bytes()
    sep = uuid.uuid4().hex
    fields = [("response_format", "verbose_json"), ("timestamp_granularities[]", "word")]
    if language:
        fields.append(("language", language))
    body = b""
    for name, val in fields:
        body += f'--{sep}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{val}\r\n'.encode()
    body += (f'--{sep}\r\nContent-Disposition: form-data; name="file"; filename="{audio.name}"\r\n'
             f"Content-Type: application/octet-stream\r\n\r\n").encode()
    body += data + f"\r\n--{sep}--\r\n".encode()
    req = urllib.request.Request(server_url.rstrip("/") + "/v1/audio/transcriptions", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={sep}"})
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            r = json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise TranscriptionError(i18n.t("err.server_status", code=e.code, detail=detail)) from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise TranscriptionTimeout(i18n.t("err.server_timeout", s=timeout_s)) from e
        raise TranscriptionError(i18n.t("err.server_unreachable", url=server_url, detail=e.reason)) from e
    except TimeoutError as e:
        raise TranscriptionTimeout(i18n.t("err.server_timeout", s=timeout_s)) from e
    if "words" not in r:
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
        return "répond"
    except (urllib.error.URLError, OSError) as e:
        raise TranscriptionError(i18n.t("err.transcription_unreachable", url=server_url, detail=e)) from e
    state = health_state(d)
    return i18n.label("state.server.", state) if state else i18n.t("state.responds")
