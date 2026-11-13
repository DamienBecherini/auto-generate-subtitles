"""Client du serveur de transcription (API compatible OpenAI, POST /v1/audio/transcriptions, verbose_json).

Le serveur doit renvoyer les mots horodatés sur la ligne de temps de l'enregistrement :
{"language", "duration", "text", "segments": [...], "words": [{"word", "start", "end"}]}. Ils sont demandés par
`timestamp_granularities[]=word`, comme dans l'API d'OpenAI.
"""
import json
import re
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import i18n


_CODE_LANGUE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")


class ErreurTranscription(Exception):
    pass


class DelaiDepasse(ErreurTranscription):
    """Le serveur n'a pas répondu à temps (ou sa réponse s'est perdue en route)."""


def transcrire(url_serveur: str, audio: Path, langue: str | None = None, delai_s: int = 7200, cle: str = "") -> dict:
    donnees = audio.read_bytes()
    sep = uuid.uuid4().hex
    champs = [("response_format", "verbose_json"), ("timestamp_granularities[]", "word")]
    if langue:
        champs.append(("language", langue))
    corps = b""
    for nom, val in champs:
        corps += f'--{sep}\r\nContent-Disposition: form-data; name="{nom}"\r\n\r\n{val}\r\n'.encode()
    corps += (f'--{sep}\r\nContent-Disposition: form-data; name="file"; filename="{audio.name}"\r\n'
              f"Content-Type: application/octet-stream\r\n\r\n").encode()
    corps += donnees + f"\r\n--{sep}--\r\n".encode()
    req = urllib.request.Request(url_serveur.rstrip("/") + "/v1/audio/transcriptions", data=corps, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={sep}"})
    if cle:
        req.add_header("Authorization", f"Bearer {cle}")
    try:
        with urllib.request.urlopen(req, timeout=delai_s) as rep:
            r = json.load(rep)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise ErreurTranscription(i18n.t("err.server_status", code=e.code, detail=detail)) from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise DelaiDepasse(i18n.t("err.server_timeout", s=delai_s)) from e
        raise ErreurTranscription(i18n.t("err.server_unreachable", url=url_serveur, detail=e.reason)) from e
    except TimeoutError as e:
        raise DelaiDepasse(i18n.t("err.server_timeout", s=delai_s)) from e
    if "words" not in r:
        raise ErreurTranscription(i18n.t("err.no_words"))
    if not _CODE_LANGUE.match(str(r.get("language") or "")):
        r["language"] = ""             # OpenAI renvoie un nom (« french ») : la langue est alors devinée d'après le texte
    return r


def sante(url_serveur: str, delai_s: int = 5) -> str:
    """État annoncé par /health. Cette route n'est pas dans l'API d'OpenAI : seul un serveur injoignable est une erreur."""
    try:
        with urllib.request.urlopen(url_serveur.rstrip("/") + "/health", timeout=delai_s) as rep:
            d = json.load(rep)
    except urllib.error.HTTPError:
        return i18n.t("state.responds_no_health")
    except ValueError:
        return "répond"
    except (urllib.error.URLError, OSError) as e:
        raise ErreurTranscription(i18n.t("err.transcription_unreachable", url=url_serveur, detail=e)) from e
    return i18n.etiquette("state.server.", d["etat"]) if isinstance(d, dict) and d.get("etat") else i18n.t("state.responds")
