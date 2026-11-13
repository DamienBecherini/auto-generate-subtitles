"""Traductions des messages (journal, erreurs, ligne de commande) et de l'interface web.

Un fichier par langue dans `locales/` (<code>.json) : des clés stables (« batch.paused ») vers le texte, avec des
paramètres nommés entre accolades (« {n} vidéos »). Un texte qui dépend d'un nombre est un objet par forme du pluriel
(catégories CLDR : one, few, many, other…), choisie d'après le paramètre `n`. Une clé absente d'une langue retombe
sur l'anglais, puis sur la clé elle-même. Ajouter une langue : copier en.json sous son code et traduire les valeurs.

Langue active : celle de la requête en cours (en-tête X-Langue de la page) pour les réponses de l'API ; sinon la
langue globale (réglage `langue_interface`, sinon la dernière langue annoncée par la page, sinon celle du système).
"""
import json
import locale
import os
import re
import threading
from pathlib import Path

DOSSIER = Path(__file__).resolve().parent / "locales"
REPLI = "en"
_catalogues: dict[str, dict] = {}
_globale: str | None = None
_local = threading.local()
_PARAM = re.compile(r"\{(\w+)\}")


def disponibles() -> list[str]:
    return sorted(f.stem for f in DOSSIER.glob("*.json"))


def catalogue(code: str) -> dict:
    if code not in _catalogues:
        try:
            _catalogues[code] = json.loads((DOSSIER / f"{code}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _catalogues[code] = {}
    return _catalogues[code]


def choisir(voulue: str | None) -> str | None:
    """Code disponible le plus proche de « fr-FR », « fr_FR.UTF-8 », « French_France »… ; None sinon."""
    if not voulue:
        return None
    v = str(voulue).strip().lower().replace("_", "-")
    dispo = disponibles()
    if v in dispo:
        return v
    if v.split("-")[0].split(".")[0] in dispo:
        return v.split("-")[0].split(".")[0]
    noms = {"french": "fr", "english": "en", "german": "de", "spanish": "es", "italian": "it", "portuguese": "pt", "dutch": "nl"}
    for nom, code in noms.items():
        if v.startswith(nom) and code in dispo:
            return code
    return None


def langue_systeme() -> str:
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        c = choisir((os.environ.get(var) or "").split(":")[0])
        if c:
            return c
    try:
        c = choisir(locale.getlocale()[0])
    except ValueError:
        c = None
    return c or REPLI


_page: str | None = None


def definir(code: str | None):
    """Langue globale choisie (réglage `langue_interface`, option --lang) ; vide ou inconnue : automatique."""
    global _globale
    _globale = choisir(code)


def annonce_page(code: str | None):
    """Langue de la page qui vient d'appeler l'API : sert au journal tant qu'aucune langue n'est imposée."""
    global _page
    _page = choisir(code) or _page


def pour_requete(code: str | None):
    """Langue des réponses du fil en cours (une requête HTTP) ; None revient à la langue globale."""
    _local.langue = choisir(code)


def langue_globale() -> str:
    return _globale or _page or langue_systeme()


def langue() -> str:
    return getattr(_local, "langue", None) or langue_globale()


def _pluriel(code: str, n) -> str:
    try:
        n = abs(float(n))
    except (TypeError, ValueError):
        return "other"
    if code == "fr":
        return "one" if n < 2 else "other"
    return "one" if n == 1 else "other"


def _texte(code: str, cle: str, n) -> str | None:
    v = catalogue(code).get(cle)
    if isinstance(v, dict):
        return v.get(_pluriel(code, n)) or v.get("other")
    return v


def t(cle: str, **params) -> str:
    code = langue()
    texte = _texte(code, cle, params.get("n"))
    if texte is None and code != REPLI:
        texte = _texte(REPLI, cle, params.get("n"))
    if texte is None:
        return cle
    return _PARAM.sub(lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), texte)


def etiquette(prefixe: str, code: str) -> str:
    """Libellé d'un code (« pret », « sous_titres ») : clé préfixe + code si elle existe, sinon le code lui-même."""
    texte = _texte(langue(), prefixe + code, None) or _texte(REPLI, prefixe + code, None)
    return texte if texte is not None else code


def nom_langue(code: str) -> str:
    """Nom d'une langue dans la langue active (« anglais », « English ») ; à défaut, son code."""
    nom = _texte(langue(), f"lang.{code}", None) or _texte(REPLI, f"lang.{code}", None)
    return nom or t("lang.unknown", code=code)


def variantes(cle: str) -> list[str]:
    """Le texte de cette clé dans toutes les langues (reconnaître une ligne de journal écrite dans une autre)."""
    return [v for c in disponibles() for v in [catalogue(c).get(cle)] if isinstance(v, str)]
