"""Crée à côté de la vidéo un `<nom>.multi.mkv` : vidéo et pistes d'origine recopiées sans réencodage, piste doublée et sous-titres ajoutés.

La vidéo d'origine n'est jamais modifiée. Le fichier n'apparaît sous son nom définitif qu'une fois vérifié.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

from . import dossier, etapes, langue, source

_CODES3: dict[str, str] = {}
for _trois, _deux in dossier._ETIQUETTES.items():
    _CODES3.setdefault(_deux, _trois)                  # la première forme est la bibliographique (fre, ger), celle de Matroska
_TEXTE_A_CONVERTIR = {"mov_text"}                      # sous-titres d'un .mp4 : Matroska ne sait pas les recopier tels quels
ECART_MAX_S = 2.0


class ErreurAssemblage(source.ErreurSource):
    pass


def code3(code: str) -> str:
    return _CODES3.get(code, "und")


def titre(code: str) -> str:
    return langue.nom(code).capitalize()


def sonde(chemin: Path) -> dict:
    r = subprocess.run([source.outil("ffprobe"), "-v", "error", "-show_entries", "stream=index,codec_type,codec_name:stream_tags=language,title:format=duration",
                        "-of", "json", str(chemin)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise ErreurAssemblage(f"lecture impossible de {Path(chemin).name} : {r.stderr[:300]}")
    return json.loads(r.stdout)


def commande(ffmpeg: str, video: Path, infos: dict, doublage: Path, cible: str, parlee: str, sous_titres: list[tuple[str, Path]], sortie: Path) -> list[str]:
    """Options ffmpeg. `sous_titres` : [(code, fichier .srt)] ; `infos` : résultat de `sonde(video)`."""
    audios = [s for s in infos["streams"] if s["codec_type"] == "audio"]
    existants = [s for s in infos["streams"] if s["codec_type"] == "subtitle"]
    n_a, n_s = len(audios), len(existants)
    cmd = [ffmpeg, "-loglevel", "error", "-y", "-i", str(video), "-i", str(doublage)]
    for _, f in sous_titres:
        cmd += ["-i", str(f)]
    cmd += ["-map", "0:v", "-map", "0:a", "-map", "0:s?", "-map", "1:a:0"]
    for i in range(len(sous_titres)):
        cmd += ["-map", f"{2 + i}:0"]
    cmd += ["-c:v", "copy", "-c:a", "copy", "-c:s", "copy"]
    for i, s in enumerate(existants):
        if s.get("codec_name") in _TEXTE_A_CONVERTIR:
            cmd += [f"-c:s:{i}", "srt"]
    for i in range(len(sous_titres)):
        cmd += [f"-c:s:{n_s + i}", "srt"]
    for i, a in enumerate(audios):
        tags = a.get("tags") or {}
        if tags.get("language", "und") == "und":
            cmd += [f"-metadata:s:a:{i}", f"language={code3(parlee)}"]
        if not tags.get("title"):
            cmd += [f"-metadata:s:a:{i}", f"title={titre(parlee)} (original)"]
        cmd += [f"-disposition:a:{i}", "default" if i == 0 else "0"]
    cmd += [f"-metadata:s:a:{n_a}", f"language={code3(cible)}", f"-metadata:s:a:{n_a}", f"title={titre(cible)} (doublage IA)", f"-disposition:a:{n_a}", "0"]
    for i, (code, _) in enumerate(sous_titres):
        cmd += [f"-metadata:s:s:{n_s + i}", f"language={code3(code)}", f"-metadata:s:s:{n_s + i}", f"title={titre(code)}", f"-disposition:s:{n_s + i}", "0"]
    return cmd + ["-f", "matroska", str(sortie)]


def verifier(mkv: Path, infos: dict, ajoutees: int):
    """Le MKV doit contenir au moins les pistes d'origine plus celles ajoutées, et durer autant que la vidéo."""
    fini, attendu = sonde(mkv), len(infos["streams"]) + ajoutees
    ecart = abs(float(fini["format"]["duration"]) - float(infos["format"]["duration"]))
    if len(fini["streams"]) != attendu or ecart > ECART_MAX_S:
        raise ErreurAssemblage(f"MKV incomplet : {len(fini['streams'])} pistes sur {attendu}, écart de durée {ecart:.1f} s")


def corbeille(fichier: Path) -> bool:
    """Envoie un fichier à la corbeille ; False si c'est impossible (le fichier reste alors en place)."""
    fichier = Path(fichier)
    try:
        if os.name == "nt":                    # chemin passé par l'environnement : ni guillemets ni accents à échapper
            script = ("Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
                      "$env:FICHIER_CORBEILLE, 'OnlyErrorDialogs', 'SendToRecycleBin')")
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env={**os.environ, "FICHIER_CORBEILLE": str(fichier)},
                           capture_output=True, timeout=600, check=True)
        elif shutil.which("gio"):
            subprocess.run(["gio", "trash", str(fichier)], capture_output=True, timeout=600, check=True)
        else:
            return False
    except (OSError, subprocess.SubprocessError):
        return False
    return not fichier.exists()


def remplacer_original(video: Path, mkv: Path, ajoutees: int, journal=print) -> Path:
    """Remplace la vidéo par le MKV, seulement s'il est vérifié et si l'original a bien rejoint la corbeille. Renvoie le fichier final.
    Les sous-titres voisins gardent le même nom de base : seule l'extension de la vidéo peut changer."""
    video, mkv = Path(video), Path(mkv)
    final = video.with_suffix(".mkv")
    if final != video and final.exists():
        journal(f"Remplacement impossible : {final.name} existe déjà. L'original et le MKV sont gardés.")
        return mkv
    verifier(mkv, sonde(video), ajoutees)
    if not corbeille(video):
        journal("Corbeille indisponible : l'original et le MKV sont gardés.")
        return mkv
    mkv.replace(final)
    journal(f"Vidéo d'origine remplacée par le MKV : {final.name} (original à la corbeille)")
    return final


def assembler(video: Path, doublage: Path, cible: str, parlee: str, sous_titres: list[tuple[str, Path]], journal=print, refaire: bool = False) -> Path:
    video = Path(video)
    sortie = etapes.chemin_mkv(video)
    if sortie.exists() and not refaire:
        journal(f"MKV déjà assemblé : {sortie.name}")
        return sortie
    partiel = sortie.with_name(sortie.name + ".part")
    infos = sonde(video)
    try:
        r = subprocess.run(commande(source.outil("ffmpeg"), video, infos, doublage, cible, parlee, sous_titres, partiel), capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            raise ErreurAssemblage(f"ffmpeg : {r.stderr[-400:]}")
        verifier(partiel, infos, 1 + len(sous_titres))
        partiel.replace(sortie)
    finally:
        partiel.unlink(missing_ok=True)
    journal(f"MKV multipiste : {sortie} ({sortie.stat().st_size / 2**20:.0f} Mo)")
    return sortie
