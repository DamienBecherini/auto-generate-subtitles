"""Temps restant d'un travail : vitesse observée, retenue d'une session à l'autre, sinon vitesse annoncée dans la configuration.

Une « passe » est un passage d'une vidéo dans la file : 1 = préparation (avec la traduction si le lot ne fait pas de
pause), 2 = traduction après une pause. Son poids dit ce qui fait varier sa durée : secondes de vidéo en passe 1,
lignes × langues en passe 2. Le taux est le nombre de secondes de calcul par unité de poids.
"""
import json
import threading

from .. import config, estimation

FICHIER = config.dossier_utilisateur() / "vitesses.json"
DUREE_MIN_S = 5                # en dessous, la passe a repris des résultats déjà faits : elle ne dit rien de la vitesse
LIGNES_PAR_SECONDE = 0.29      # sous-titres par seconde de vidéo (mesuré : 494 et 562 lignes pour 30 min)

_verrou = threading.Lock()
_memoire: dict | None = None


def cle(passe: int, options: dict) -> str:
    """La vitesse dépend de ce qui travaille : serveur de transcription, traducteur, ou les deux à la suite."""
    transcription, traducteur = options.get("transcription") or "", options.get("traducteur") or ""
    if passe == 2:
        return f"traduction|{traducteur}"
    return f"complet|{transcription}|{traducteur}" if options.get("pause", True) is False else f"transcription|{transcription}"


def _charger() -> dict:
    global _memoire
    if _memoire is None:
        try:
            _memoire = json.loads(FICHIER.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _memoire = {}
    return _memoire


def lire(c: str) -> float | None:
    with _verrou:
        return _charger().get(c)


def noter(c: str, taux: float):
    """Moyenne avec la valeur retenue : la mémoire suit les changements de machine sans dépendre d'une seule vidéo."""
    with _verrou:
        m = _charger()
        m[c] = (m[c] + taux) / 2 if c in m else taux
        try:
            FICHIER.parent.mkdir(parents=True, exist_ok=True)
            FICHIER.write_text(json.dumps(m, indent=1), encoding="utf-8")
        except OSError:
            pass                       # sans mémoire, la vitesse sera réapprise


def _taux_annonce(passe: int, options: dict) -> float | None:
    """Tant que rien n'a été mesuré : vitesse du serveur de transcription et durée par ligne de la configuration."""
    try:
        cfg = config.charger()
        par_ligne = float(estimation.reglages(cfg)["secondes_par_ligne_local"])
        traducteur = config.moteur(cfg, "traducteurs", "traducteur_actif", options.get("traducteur") or None)
        avec_llm = traducteur.get("type", "openai") == "openai"
        if passe == 2:
            return par_ligne if avec_llm else None
        serveur = config.moteur(cfg, "transcriptions", "transcription_active", options.get("transcription") or None)
        vitesse = float(serveur.get("vitesse") or 0)
        if vitesse <= 0:
            return None
        taux = 1 / vitesse
        if options.get("pause", True) is False and avec_llm:
            taux += LIGNES_PAR_SECONDE * par_ligne * max(1, len(options.get("langues") or []))
        return taux
    except (config.ErreurConfig, OSError, ValueError, KeyError, TypeError):
        return None


def taux_connu(passe: int, options: dict) -> float | None:
    return lire(cle(passe, options)) or _taux_annonce(passe, options)
