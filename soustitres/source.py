"""Obtention de la vidéo et de son audio : adresse YouTube (yt-dlp) ou fichier sur le disque (ffmpeg).

Chemins toujours avec des barres obliques normales (pathlib) : le même code tourne sous Windows et dans WSL.
"""
import json
import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path


class ErreurSource(Exception):
    pass


EXTENSIONS_AUDIO = {".m4a", ".opus", ".mp3", ".ogg", ".flac", ".wav"}


def est_audio_extrait(p: Path, base: str) -> bool:
    """Fichier audio d'un dossier de travail : <base>.audio.<ext>, ou <base>.<ext> (nom des premières versions)."""
    return p.suffix.lower() in EXTENSIONS_AUDIO and (p.name.startswith(base + ".audio.") or p.stem == base)


def est_url(s: str) -> bool:
    return re.match(r"https?://", s) is not None


def outil(nom: str) -> str:
    chemin = shutil.which(nom)
    if not chemin:
        raise ErreurSource(f"{nom} introuvable dans le PATH")
    return chemin


def slug(titre: str, maxi: int = 80) -> str:
    """Nom de dossier et de fichier sûr : lettres sans accent, chiffres, points et tirets."""
    sans_accents = unicodedata.normalize("NFKD", titre).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9.]+", "-", sans_accents).strip("-.")
    s = re.sub(r"-{2,}", "-", s)
    return s[:maxi].rstrip("-.") or "video"


def _ffprobe(fichier: Path) -> dict:
    r = subprocess.run([outil("ffprobe"), "-v", "error", "-print_format", "json", "-show_streams", "-show_format",
                        str(fichier)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise ErreurSource(f"ffprobe : {r.stderr.strip()[-300:]}")
    return json.loads(r.stdout)


def types_de_flux(fichier: Path) -> set[str]:
    return {s.get("codec_type") for s in _ffprobe(fichier).get("streams", [])}


def duree(fichier: Path) -> float:
    try:
        return float(_ffprobe(fichier)["format"]["duration"])
    except (KeyError, ValueError):
        return 0.0


# Conteneur qui accepte le codec tel quel (copie sans réencodage) ; sinon réencodage en AAC.
_CONTENEURS = {"aac": "m4a", "opus": "opus", "mp3": "mp3", "vorbis": "ogg", "flac": "flac"}


def extraire_audio(video: Path, destination_sans_extension: Path) -> Path:
    """Piste audio de la vidéo dans un fichier à part (copie du flux quand le conteneur le permet)."""
    flux = [s for s in _ffprobe(video).get("streams", []) if s.get("codec_type") == "audio"]
    if not flux:
        raise ErreurSource(f"aucune piste audio dans {video}")
    codec = flux[0].get("codec_name", "")
    ext = _CONTENEURS.get(codec)
    # Pas with_suffix : « nom.audio » perdrait son « .audio » (le fichier doit s'appeler <nom>.audio.<ext>)
    cible = destination_sans_extension.parent / f"{destination_sans_extension.name}.{ext or 'm4a'}"
    codec_args = ["-c:a", "copy"] if ext else ["-c:a", "aac", "-b:a", "128k"]
    r = subprocess.run([outil("ffmpeg"), "-y", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(video),
                        "-vn", "-map", "0:a:0", *codec_args, str(cible)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 or not cible.exists():
        raise ErreurSource(f"ffmpeg : {r.stderr.strip()[-300:]}")
    return cible


def _commande(commande: list[str]) -> list[str]:
    """Vérifie le premier mot de la commande (wsl ou yt-dlp) dans le PATH."""
    return [outil(commande[0]), *commande[1:]]


def chemin_local(chemin: str, commande: list[str]) -> Path:
    """Un chemin rendu par yt-dlp dans WSL (/mnt/d/…) devient un chemin Windows (D:/…) quand la commande passe par wsl."""
    if os.name == "nt" and Path(commande[0]).stem.lower() == "wsl":
        # Les liens symboliques (~/ytdl → /mnt/d/…) sont résolus dans WSL ; le fichier peut ne pas exister encore.
        r = subprocess.run([commande[0], "-e", "readlink", "-m", chemin], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0 and r.stdout.strip():
            chemin = r.stdout.strip()
        m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", chemin)
        if not m:
            raise ErreurSource(f"yt-dlp écrit hors d'un disque Windows ({chemin}) : régler -o dans ~/.config/yt-dlp/config sur /mnt/…")
        return Path(f"{m.group(1).upper()}:/{m.group(2)}")
    return Path(chemin)


def chemin_distant(chemin: Path, commande: list[str]) -> str:
    """Chemin Windows → chemin vu par yt-dlp dans WSL (D:/x → /mnt/d/x) quand la commande passe par wsl."""
    s = chemin.as_posix()
    if os.name == "nt" and Path(commande[0]).stem.lower() == "wsl":
        m = re.match(r"^([a-zA-Z]):/(.*)$", s)
        if not m:
            raise ErreurSource(f"dossier de sortie hors d'un disque Windows : {chemin}")
        return f"/mnt/{m.group(1).lower()}/{m.group(2)}"
    return s


def _options_sortie(commande: list[str], dossier: Path | None) -> list[str]:
    """Remplace le -o de la config de yt-dlp par le dossier de sortie demandé (nom = titre, extension finale)."""
    if dossier is None:
        return []
    return ["-o", chemin_distant(dossier, commande) + "/%(title)s.%(ext)s"]


def infos_youtube(url: str, commande: list[str], dossier: Path | None = None) -> dict:
    """Identifiant, titre, durée (secondes) et fichier de destination, sans télécharger."""
    r = subprocess.run([*_commande(commande), "--no-playlist", "--no-download", *_options_sortie(commande, dossier),
                        "--print", "%(.{id,title,duration,filename})j", url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise ErreurSource(f"yt-dlp : {(r.stderr or r.stdout).strip()[-500:]}")
    infos = json.loads(r.stdout.strip().splitlines()[-1])
    infos["video"] = chemin_local(infos["filename"], commande)
    return infos


def telecharger_youtube(url: str, commande: list[str], journal=print, dossier: Path | None = None) -> Path:
    """Télécharge la vidéo avec yt-dlp (dans WSL par défaut) : format et fusion MP4 viennent de la config de
    yt-dlp (~/.config/yt-dlp/config) ; le dossier de sortie remplace le sien si `dossier` est donné. Une vidéo déjà
    présente n'est pas retéléchargée. L'audio est ensuite extrait du MP4 par ffmpeg, comme pour un fichier."""
    infos = infos_youtube(url, commande, dossier)
    video = infos["video"]
    if video.exists():
        journal(f"Vidéo déjà téléchargée : {video}")
        return video
    journal(f"Téléchargement : {infos.get('title')} ({infos.get('duration') or '?'} s) → {video}")
    r = subprocess.run([*_commande(commande), "--no-playlist", *_options_sortie(commande, dossier), url])
    if r.returncode != 0:
        raise ErreurSource(f"yt-dlp a échoué (code {r.returncode})")
    if not video.exists():
        raise ErreurSource(f"fichier attendu absent après le téléchargement : {video}")
    return video
