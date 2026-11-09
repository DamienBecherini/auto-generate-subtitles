"""Nettoyage des dossiers de travail (`<sortie>/.travail/<nom>/`) par catégorie de fichiers.

Catégories (options `[nettoyage]` de la configuration, `true` = supprimer) :
  audio          `<nom>.audio.*`                        la piste extraite, le plus gros fichier (refaite au besoin, quelques secondes)
  sous_titres    `*.srt`, `*.vtt`                        les copies du dossier de travail (les fichiers utiles sont à côté de la vidéo)
  transcription  `transcription.json`, `traduction*.json`  données brutes : gardées par défaut, elles permettent d'ajouter une
                                                        langue ou de changer la mise en forme sans retranscrire ni retraduire
  journaux       `journal-*.txt`                         gardés par défaut ; ceux de plus de `journaux_age_h` heures sont purgés
                                                        au démarrage de l'interface et par « Nettoyer maintenant »
"""
import time
from pathlib import Path

CATEGORIES = {
    "audio": ["*.audio.*", "*.m4a", "*.opus", "*.mp3", "*.ogg", "*.flac", "*.wav"],   # extensions seules : nom des premières versions
    "sous_titres": ["*.srt", "*.vtt"],
    "transcription": ["transcription.json", "traduction*.json"],
    "journaux": ["journal-*.txt"],
}
DEFAUTS = {"audio": True, "sous_titres": True, "transcription": False, "journaux": False, "journaux_age_h": 48}


def options(cfg: dict) -> dict:
    return {**DEFAUTS, **(cfg.get("nettoyage") or {})}


def classer(travail: Path) -> dict[str, list[Path]]:
    """Fichiers d'un dossier de travail par catégorie."""
    r = {}
    for cat, motifs in CATEGORIES.items():
        vus = []
        for motif in motifs:
            vus.extend(p for p in travail.glob(motif) if p.is_file())
        r[cat] = sorted(set(vus))
    return r


def _supprimer(fichiers: list[Path]) -> tuple[int, int]:
    nb = octets = 0
    for f in fichiers:
        try:
            taille = f.stat().st_size
            f.unlink()
            nb += 1
            octets += taille
        except OSError:
            pass
    return nb, octets


def nettoyer_dossier(travail: Path, opts: dict, journal=lambda _m: None, garder: set[Path] | None = None,
                     journaux_vieux_seulement: bool = True) -> tuple[int, int]:
    """Applique les options à un dossier de travail. `garder` : fichiers à ne pas toucher (seule copie d'un résultat).
    Les journaux sont supprimés seulement s'ils ont plus de `journaux_age_h` heures (sauf demande contraire)."""
    if not travail.is_dir():
        return 0, 0
    garder = {p.resolve() for p in (garder or set())}
    limite = time.time() - float(opts.get("journaux_age_h", 48)) * 3600
    total_nb = total_octets = 0
    for cat, fichiers in classer(travail).items():
        if not opts.get(cat):
            continue
        cibles = [f for f in fichiers if f.resolve() not in garder]
        if cat == "journaux" and journaux_vieux_seulement:
            cibles = [f for f in cibles if f.stat().st_mtime <= limite]
        nb, octets = _supprimer(cibles)
        if nb:
            journal(f"Nettoyage {cat} : {nb} fichier(s), {octets / 1e6:.1f} Mo")
        total_nb += nb
        total_octets += octets
    return total_nb, total_octets


def purger_journaux(racine: Path, age_h: float) -> tuple[int, int]:
    """Journaux de plus de `age_h` heures dans tous les dossiers de travail."""
    if not racine.is_dir():
        return 0, 0
    limite = time.time() - age_h * 3600
    return _supprimer([f for f in racine.glob("*/journal-*.txt") if f.is_file() and f.stat().st_mtime <= limite])


def bilan(racine: Path) -> dict[str, dict]:
    """Nombre de fichiers et octets par catégorie sur tous les dossiers de travail."""
    r = {cat: {"fichiers": 0, "octets": 0} for cat in CATEGORIES}
    if racine.is_dir():
        for d in racine.iterdir():
            if d.is_dir():
                for cat, fichiers in classer(d).items():
                    r[cat]["fichiers"] += len(fichiers)
                    r[cat]["octets"] += sum(f.stat().st_size for f in fichiers)
    return r


def nettoyer_tout(racine: Path, opts: dict) -> dict[str, dict]:
    """« Nettoyer maintenant » : applique les options à tous les dossiers de travail (journaux : seulement les vieux),
    puis supprime les dossiers vides. Retourne {catégorie: {fichiers, octets}} supprimés."""
    r = {cat: {"fichiers": 0, "octets": 0} for cat in CATEGORIES}
    if not racine.is_dir():
        return r
    for d in sorted(p for p in racine.iterdir() if p.is_dir()):
        avant = classer(d)
        tailles = {f: f.stat().st_size for fichiers in avant.values() for f in fichiers}
        nettoyer_dossier(d, opts)
        apres = classer(d)
        for cat in CATEGORIES:
            restants = set(apres[cat])
            partis = [f for f in avant[cat] if f not in restants]
            r[cat]["fichiers"] += len(partis)
            r[cat]["octets"] += sum(tailles[f] for f in partis)
        try:
            if not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass
    return r
