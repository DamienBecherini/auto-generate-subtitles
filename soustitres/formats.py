"""Écriture des fichiers de sous-titres SRT et WebVTT."""
from .decoupe import Reglages, SousTitre, couper_lignes


def hms(t: float, separateur: str = ",") -> str:
    t = max(0.0, t)
    ms = round(t * 1000)
    h, reste = divmod(ms, 3_600_000)
    m, reste = divmod(reste, 60_000)
    s, ms = divmod(reste, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{separateur}{ms:03d}"


def srt(st: list[SousTitre], r: Reglages | None = None) -> str:
    r = r or Reglages()
    blocs = []
    for i, s in enumerate(st, 1):
        blocs.append(f"{i}\n{hms(s.debut)} --> {hms(s.fin)}\n" + "\n".join(couper_lignes(s.texte, r)) + "\n")
    return "\n".join(blocs)


def vtt(st: list[SousTitre], r: Reglages | None = None) -> str:
    r = r or Reglages()
    blocs = ["WEBVTT\n"]
    for s in st:
        blocs.append(f"{hms(s.debut, '.')} --> {hms(s.fin, '.')}\n" + "\n".join(couper_lignes(s.texte, r)) + "\n")
    return "\n".join(blocs)
