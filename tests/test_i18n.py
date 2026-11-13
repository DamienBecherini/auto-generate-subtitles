"""Translation catalogs: same keys and same parameters in every language, no key used that is missing."""
import json
import re
from pathlib import Path

import pytest

from subtitles import i18n
from subtitles.web import timing

PACKAGE = Path(i18n.__file__).resolve().parent
PARAM = re.compile(r"\{(\w+)\}")


def _catalog(code):
    return json.loads((i18n.LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))


def _parameters(value) -> set[str]:
    texts = value.values() if isinstance(value, dict) else [value]
    return {p for t in texts for p in PARAM.findall(t)}


def test_at_least_french_and_english():
    assert {"fr", "en"} <= set(i18n.available())


@pytest.mark.parametrize("code", [c for c in i18n.available() if c != i18n.FALLBACK])
def test_same_keys_and_parameters_as_english(code):
    ref, other = _catalog(i18n.FALLBACK), _catalog(code)
    assert set(other) - set(ref) == set(), "keys missing from en.json"
    assert set(ref) - set(other) == set(), f"keys to translate in {code}.json"
    different = [k for k in ref if _parameters(ref[k]) != _parameters(other[k])]
    assert different == []


@pytest.mark.parametrize("code", i18n.available())
def test_plurals_have_an_other_form(code):
    without = [k for k, v in _catalog(code).items() if isinstance(v, dict) and "other" not in v]
    assert without == []


def _used_keys() -> set[str]:
    keys = set()
    for f in PACKAGE.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        keys |= set(re.findall(r"""["']((?:err|log|state|est|cli|srv|dialog|test|mkv|activity|step|cfg)\.[a-z0-9_.]*[a-z0-9_])["']""", text))
    static = PACKAGE / "web" / "static"
    for f in static.glob("*.js"):
        keys |= set(re.findall(r"""["']((?:ui|log|est)\.[a-z0-9_.]*[a-z0-9_])["']""", f.read_text(encoding="utf-8")))
    keys |= set(re.findall(r'data-i18n(?:-html|-title|-placeholder|-aria-label)?="([^"]+)"', (static / "index.html").read_text(encoding="utf-8")))
    return keys


def test_every_used_key_exists():
    ref = _catalog(i18n.FALLBACK)
    missing = sorted(_used_keys() - set(ref))
    assert missing == []


def test_t_plural_parameters_and_fallback():
    i18n.set_language("fr")
    assert i18n.t("log.review_to_check", n=1) == "Relecture IA : 1 nom à vérifier"
    assert i18n.t("log.review_to_check", n=3) == "Relecture IA : 3 noms à vérifier"
    i18n.set_language("en")
    assert i18n.t("log.review_to_check", n=1) == "AI review: 1 name to check"
    assert i18n.t("log.review_to_check", n=0) == "AI review: 0 names to check"
    assert i18n.t("key.unknown") == "key.unknown"
    assert i18n.t("err.file_not_found") == "file not found: {path}"     # missing parameter: left as it is
    assert i18n.language_name("de") == "German" and i18n.language_name("xx") == "language with code “xx”"


def test_choice_of_the_language():
    assert i18n.pick("fr-FR") == "fr" and i18n.pick("en_US.UTF-8") == "en" and i18n.pick("French_France") == "fr"
    assert i18n.pick("zz") is None and i18n.pick("") is None
    i18n.set_language(None)
    i18n.note_page_language("en-GB")
    assert i18n.language() == "en"                     # nothing imposed: the language of the page
    i18n.set_language("fr")
    assert i18n.language() == "fr"                     # language imposed by the setting
    i18n.for_request("en")
    try:
        assert i18n.language() == "en"                 # the request wins for its answer
    finally:
        i18n.for_request(None)


def test_log_duration_recognises_the_pauses_in_every_language():
    fr = ["[10:00:00] En attente : v.mp4", "[10:05:00] En pause : choisir le traducteur…", "[11:00:00] Reprise : traduction…", "[11:02:00] Traduit en 120 s"]
    en = ["[10:00:00] Waiting: v.mp4", "[10:05:00] Paused: choose the translator…", "[11:00:00] Resumed: translation…", "[11:02:00] Translated in 120 s"]
    assert timing.log_duration(fr) == timing.log_duration(en) == 7 * 60
