"""Doublage IA : lance `doublage_travail.py` dans l'environnement séparé qui contient torch, demucs et OmniVoice.

Table `[doublage]` de la configuration : `actif`, `commande` (interpréteur Python de cet environnement), `modele`, `wsl`
(chemins traduits en /mnt/… quand la commande passe par WSL). La première ligne envoyée au programme est son travail en
JSON ; l'entrée reste ensuite ouverte : sa fermeture (annulation, arrêt du service) lui dit de s'arrêter.
"""
import json
import re
import subprocess
from collections import deque
from pathlib import Path

from . import etapes, i18n

PROGRAMME = Path(__file__).with_name("doublage_travail.py")
PREFIXE = "» "                                  # lignes du programme destinées au journal


class ErreurDoublage(Exception):
    pass


def disponible(cfg: dict) -> bool:
    d = cfg.get("doublage") or {}
    return bool(d.get("actif") and d.get("commande") and d.get("modele"))


def chemin_pour(cfg: dict, p: str | Path) -> str:
    """Chemin vu par le programme : D:/x devient /mnt/d/x derrière WSL."""
    texte = str(p).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", texte)
    return f"/mnt/{m.group(1).lower()}/{m.group(2)}" if m and (cfg.get("doublage") or {}).get("wsl") else texte


def message_travail(brut: str) -> str:
    """Ligne du programme de doublage : {"cle": …, paramètres} traduit dans la langue active ; texte tel quel sinon."""
    try:
        d = json.loads(brut)
    except ValueError:
        return brut
    if not isinstance(d, dict) or "cle" not in d:
        return brut
    return i18n.t(d.pop("cle"), **d)


def doubler(cfg: dict, video: Path, travail: Path, srt_origine: Path, srt_cible: Path, cible: str, journal=print, annule=lambda: False, refaire: bool = False) -> Path:
    """Fabrique `doublage/<cible>.m4a` dans le dossier de travail (voix clonée mêlée au fond sonore d'origine) et renvoie son chemin."""
    sortie = etapes.fichier_doublage(travail, cible)
    if sortie.is_file() and not refaire:
        journal(i18n.t("log.dub_already_done", lang=cible, file=sortie.name))
        return sortie
    if not disponible(cfg):
        raise ErreurDoublage(i18n.t("err.dub_not_configured"))
    sortie.parent.mkdir(parents=True, exist_ok=True)
    conf = cfg["doublage"]
    c = lambda p: chemin_pour(cfg, p)  # noqa: E731
    travail_json = {"video": c(video), "srt_origine": c(srt_origine), "srt_cible": c(srt_cible), "langue": cible, "sortie": c(sortie),
                    "temporaire": c(sortie.parent / f"temp-{cible}"), "modele": conf["modele"],
                    "pas": int(conf.get("pas", 32))}
    try:
        p = subprocess.Popen([*conf["commande"], c(PROGRAMME)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    except OSError as e:
        raise ErreurDoublage(i18n.t("err.dub_program_not_found", error=e)) from e
    p.stdin.write((json.dumps(travail_json, ensure_ascii=False) + "\n").encode("utf-8"))
    p.stdin.flush()
    fin, annulee = deque(maxlen=15), False
    tampon = b""
    while True:
        bloc = p.stdout.read(4096)
        if not bloc:
            break
        tampon += bloc
        *lignes, tampon = re.split(rb"[\r\n]+", tampon)
        for brute in lignes:
            ligne = brute.decode("utf-8", "replace")
            if ligne.startswith(PREFIXE):
                journal(message_travail(ligne[len(PREFIXE):]))
            elif ligne.strip():
                fin.append(ligne)
        if annule() and not annulee:
            annulee = True
            p.stdin.close()                     # le programme s'arrête dès que son entrée se ferme
            p.terminate()
    code = p.wait()
    if annulee:
        raise ErreurDoublage(i18n.t("err.cancelled"))
    if code or not sortie.is_file():
        raise ErreurDoublage(i18n.t("err.dub_failed", code=code, detail=" | ".join(list(fin)[-4:])[-500:]))
    return sortie
