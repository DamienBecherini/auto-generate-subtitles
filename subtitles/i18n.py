"""Translations of the messages (log, errors, command line) and of the web interface.

One file per language in `locales/` (<code>.json): stable keys ("batch.paused") to the text, with named parameters
in braces ("{n} videos"). A text that depends on a number is an object with one entry per plural form (CLDR
categories: one, few, many, other…), chosen from the parameter `n`. A key missing from a language falls back to
English, then to the key itself. Adding a language: copy en.json under its code and translate the values.

Active language: the one of the current request (X-Language header sent by the page) for API answers; otherwise the
global language (`ui_language` setting, else the last language announced by the page, else the system's).
"""
import json
import locale
import os
import re
import threading
from pathlib import Path

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
FALLBACK = "en"
_catalogs: dict[str, dict] = {}
_global: str | None = None
_local = threading.local()
_PARAM = re.compile(r"\{(\w+)\}")


def available() -> list[str]:
    return sorted(f.stem for f in LOCALES_DIR.glob("*.json"))


def catalog(code: str) -> dict:
    if code not in _catalogs:
        try:
            _catalogs[code] = json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _catalogs[code] = {}
    return _catalogs[code]


def pick(wanted: str | None) -> str | None:
    """Closest available code to "fr-FR", "fr_FR.UTF-8", "French_France"…; None otherwise."""
    if not wanted:
        return None
    v = str(wanted).strip().lower().replace("_", "-")
    avail = available()
    if v in avail:
        return v
    if v.split("-")[0].split(".")[0] in avail:
        return v.split("-")[0].split(".")[0]
    names = {"french": "fr", "english": "en", "german": "de", "spanish": "es", "italian": "it", "portuguese": "pt", "dutch": "nl"}
    for name, code in names.items():
        if v.startswith(name) and code in avail:
            return code
    return None


def system_language() -> str:
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        c = pick((os.environ.get(var) or "").split(":")[0])
        if c:
            return c
    try:
        c = pick(locale.getlocale()[0])
    except ValueError:
        c = None
    return c or FALLBACK


_page: str | None = None


def set_language(code: str | None):
    """Chosen global language (`ui_language` setting, --lang option); empty or unknown: automatic."""
    global _global
    _global = pick(code)


def note_page_language(code: str | None):
    """Language of the page that just called the API: used for the log as long as no language is imposed."""
    global _page
    _page = pick(code) or _page


def for_request(code: str | None):
    """Language of the answers of the current thread (one HTTP request); None goes back to the global language."""
    _local.language = pick(code)


def global_language() -> str:
    return _global or _page or system_language()


def language() -> str:
    return getattr(_local, "language", None) or global_language()


def _plural(code: str, n) -> str:
    try:
        n = abs(float(n))
    except (TypeError, ValueError):
        return "other"
    if code == "fr":
        return "one" if n < 2 else "other"
    return "one" if n == 1 else "other"


def _text(code: str, key: str, n) -> str | None:
    v = catalog(code).get(key)
    if isinstance(v, dict):
        return v.get(_plural(code, n)) or v.get("other")
    return v


def t(key: str, **params) -> str:
    code = language()
    text = _text(code, key, params.get("n"))
    if text is None and code != FALLBACK:
        text = _text(FALLBACK, key, params.get("n"))
    if text is None:
        return key
    return _PARAM.sub(lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), text)


def label(prefix: str, code: str) -> str:
    """Label of a code ("ready", "subtitles"): key prefix + code if it exists, else the code itself."""
    text = _text(language(), prefix + code, None) or _text(FALLBACK, prefix + code, None)
    return text if text is not None else code


def language_name(code: str) -> str:
    """Name of a language in the active language ("anglais", "English"); failing that, its code."""
    name = _text(language(), f"lang.{code}", None) or _text(FALLBACK, f"lang.{code}", None)
    return name or t("lang.unknown", code=code)


def variants(key: str) -> list[str]:
    """The text of this key in every language (to recognise a log line written in another one)."""
    return [v for c in available() for v in [catalog(c).get(key)] if isinstance(v, str)]
