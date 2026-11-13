"""Catalogues de traductions : mêmes clés et mêmes paramètres dans toutes les langues, aucune clé utilisée qui manque."""
import json
import re
from pathlib import Path

import pytest

from soustitres import i18n
from soustitres.web import temps

PAQUET = Path(i18n.__file__).resolve().parent
PARAM = re.compile(r"\{(\w+)\}")


def _catalogue(code):
    return json.loads((i18n.DOSSIER / f"{code}.json").read_text(encoding="utf-8"))


def _parametres(valeur) -> set[str]:
    textes = valeur.values() if isinstance(valeur, dict) else [valeur]
    return {p for t in textes for p in PARAM.findall(t)}


def test_au_moins_francais_et_anglais():
    assert {"fr", "en"} <= set(i18n.disponibles())


@pytest.mark.parametrize("code", [c for c in i18n.disponibles() if c != i18n.REPLI])
def test_memes_cles_et_parametres_que_l_anglais(code):
    ref, autre = _catalogue(i18n.REPLI), _catalogue(code)
    assert set(autre) - set(ref) == set(), "clés absentes de en.json"
    assert set(ref) - set(autre) == set(), f"clés à traduire dans {code}.json"
    differents = [k for k in ref if _parametres(ref[k]) != _parametres(autre[k])]
    assert differents == []


@pytest.mark.parametrize("code", i18n.disponibles())
def test_pluriels_avec_forme_other(code):
    sans = [k for k, v in _catalogue(code).items() if isinstance(v, dict) and "other" not in v]
    assert sans == []


def _cles_utilisees() -> set[str]:
    cles = set()
    for f in PAQUET.rglob("*.py"):
        texte = f.read_text(encoding="utf-8")
        cles |= set(re.findall(r"""["']((?:err|log|state|est|cli|srv|dialog|test|mkv|activity|step|cfg)\.[a-z0-9_.]*[a-z0-9_])["']""", texte))
    statique = PAQUET / "web" / "static"
    for f in statique.glob("*.js"):
        cles |= set(re.findall(r"""["']((?:ui|log|est)\.[a-z0-9_.]*[a-z0-9_])["']""", f.read_text(encoding="utf-8")))
    cles |= set(re.findall(r'data-i18n(?:-html|-title|-placeholder|-aria-label)?="([^"]+)"', (statique / "index.html").read_text(encoding="utf-8")))
    return cles


def test_toutes_les_cles_utilisees_existent():
    ref = _catalogue(i18n.REPLI)
    manquantes = sorted(_cles_utilisees() - set(ref))
    assert manquantes == []


def test_t_pluriel_parametres_et_repli():
    i18n.definir("fr")
    assert i18n.t("log.review_to_check", n=1) == "Relecture IA : 1 nom à vérifier"
    assert i18n.t("log.review_to_check", n=3) == "Relecture IA : 3 noms à vérifier"
    i18n.definir("en")
    assert i18n.t("log.review_to_check", n=1) == "AI review: 1 name to check"
    assert i18n.t("log.review_to_check", n=0) == "AI review: 0 names to check"
    assert i18n.t("cle.inconnue") == "cle.inconnue"
    assert i18n.t("err.file_not_found") == "file not found: {path}"     # paramètre absent : laissé tel quel
    assert i18n.nom_langue("de") == "German" and i18n.nom_langue("xx") == "language with code “xx”"


def test_choix_de_la_langue():
    assert i18n.choisir("fr-FR") == "fr" and i18n.choisir("en_US.UTF-8") == "en" and i18n.choisir("French_France") == "fr"
    assert i18n.choisir("zz") is None and i18n.choisir("") is None
    i18n.definir(None)
    i18n.annonce_page("en-GB")
    assert i18n.langue() == "en"                       # rien d'imposé : la langue de la page
    i18n.definir("fr")
    assert i18n.langue() == "fr"                       # langue imposée par le réglage
    i18n.pour_requete("en")
    try:
        assert i18n.langue() == "en"                   # la requête l'emporte pour sa réponse
    finally:
        i18n.pour_requete(None)


def test_duree_journal_reconnait_les_pauses_dans_toutes_les_langues():
    fr = ["[10:00:00] En attente : v.mp4", "[10:05:00] En pause : choisir le traducteur…", "[11:00:00] Reprise : traduction…", "[11:02:00] Traduit en 120 s"]
    en = ["[10:00:00] Waiting: v.mp4", "[10:05:00] Paused: choose the translator…", "[11:00:00] Resumed: translation…", "[11:02:00] Translated in 120 s"]
    assert temps.duree_journal(fr) == temps.duree_journal(en) == 7 * 60
