"""Étapes d'une vidéo : Transcription → Relecture IA → Validation → Traduction → Doublage → Assemblage.

Ce qui est fait se lit sur le disque, pour survivre à un redémarrage du service : une étape terminée n'est jamais refaite.
`ARRETS` : jusqu'où un lot va tout seul.
"""
import json
from pathlib import Path

from . import relecture

ORDRE = ("transcription", "relecture", "validation", "traduction", "doublage", "assemblage")
ARRETS = ("validation", "traduction", "doublage", "assemblage")
SUFFIXE_MKV = ".multi.mkv"


def va_jusqua(arret: str | None, etape: str) -> bool:
    """L'étape fait-elle partie du travail demandé ? Sans précision, la chaîne s'arrête à la traduction."""
    return ORDRE.index(etape) <= ORDRE.index(arret or "traduction")


def chemin_mkv(video: Path) -> Path:
    return Path(video).with_name(Path(video).stem + SUFFIXE_MKV)


def fichier_doublage(travail: Path, cible: str) -> Path:
    """Dans un sous-dossier : le nettoyage du dossier de travail supprime les .m4a de premier niveau (audio extrait)."""
    return Path(travail) / "doublage" / f"{cible}.m4a"


def marque_integration(travail: Path) -> Path:
    """Présent quand le MKV a remplacé la vidéo d'origine : plus de .multi.mkv à côté, mais le doublage est fait."""
    return Path(travail) / "doublage" / "integre.txt"


def _traduction_conservee(travail: Path, video: Path, cible: str) -> bool:
    if (travail / f"traduction.{cible}.json").is_file() or video.with_name(f"{video.stem}.{cible}.srt").is_file():
        return True
    return cible == "fr" and (travail / "traduction.json").is_file()   # ancien nom


def constater(travail: Path, video: str | Path, langues: list[str], parlee: str, attente: set[str]) -> dict:
    """Étapes faites pour une vidéo. `attente` : noms encore à valider dans le lot (voir relecture.noms_en_attente)."""
    travail, video = Path(travail), Path(video)
    cibles = [c for c in langues if c != parlee]
    fichier_relu = travail / relecture.FICHIER_VIDEO
    relue, validee = False, False
    try:
        noms = json.loads(fichier_relu.read_text(encoding="utf-8")).get("noms", [])
        relue, validee = True, not attente.intersection(noms)
    except (OSError, ValueError, AttributeError):
        pass
    assemblee = chemin_mkv(video).is_file() or marque_integration(travail).is_file()
    return {"transcrite": (travail / "transcription.json").is_file(), "relue": relue, "validee": validee,
            "traduite": bool(cibles) and all(_traduction_conservee(travail, video, c) for c in cibles),
            "doublee": assemblee or (bool(cibles) and fichier_doublage(travail, cibles[0]).is_file()), "assemblee": assemblee}
