"""Temps restant d'un travail : vitesse observée, retenue d'une session à l'autre, sinon vitesse annoncée dans la configuration.

Une « passe » est un passage d'une vidéo dans la file : 1 = préparation (avec la traduction si le lot ne fait pas de
pause), 2 = traduction après une pause, 3 = doublage et assemblage. Son poids dit ce qui fait varier sa durée : secondes
de vidéo en passes 1 et 3, lignes × langues en passe 2. Le taux est le nombre de secondes de calcul par unité de poids.
"""
import json
import re
import threading

from .. import config, estimation, i18n

FICHIER = config.dossier_utilisateur() / "vitesses.json"
DUREE_MIN_S = 5                # en dessous, la passe a repris des résultats déjà faits : elle ne dit rien de la vitesse
LIGNES_PAR_SECONDE = 0.29      # sous-titres par seconde de vidéo (mesuré : 494 et 562 lignes pour 30 min)
DOUBLAGE_PAR_SECONDE = 0.28    # secondes de carte graphique par seconde de vidéo (mesuré : 8 min 30 pour 30 min)

_verrou = threading.Lock()
_memoire: dict | None = None


def cle(passe: int, options: dict) -> str:
    """La vitesse dépend de ce qui travaille : serveur de transcription, traducteur, ou les deux à la suite."""
    transcription, traducteur = options.get("transcription") or "", options.get("traducteur") or ""
    if passe == 3:
        return "doublage"
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
    if passe == 3:
        return DOUBLAGE_PAR_SECONDE
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


_HEURE = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] ")


def duree_journal(lignes: list[str]) -> int:
    """Durée d'une tâche lue dans son journal : de la première à la dernière ligne datée, sans le temps passé en pause.
    Les marques de pause et de reprise sont reconnues dans toutes les langues (un journal garde celle de son écriture)."""
    pauses = tuple("] " + v for v in i18n.variantes("log.pause_prefix"))
    reprises = tuple("] " + v for v in i18n.variantes("log.resume_prefix"))
    total, precedente, en_pause = 0, None, False
    for ligne in lignes:
        m = _HEURE.match(ligne)
        if not m:
            continue
        s = 3600 * int(m[1]) + 60 * int(m[2]) + int(m[3])
        if precedente is not None and not en_pause:
            total += (s - precedente) % 86400
        precedente = s
        corps = ligne[m.end() - 2:]
        if corps.startswith(pauses):
            en_pause = True
        elif corps.startswith(reprises):
            en_pause = False
    return total
