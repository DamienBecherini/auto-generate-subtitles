"""Un dossier comme source : inventaire des vidéos et des sous-titres déjà présents à côté d'elles, langue de la piste
audio (étiquette ffprobe quand elle existe), et fichier de lot pour reprendre un travail long après une coupure.

Sous-titres reconnus à côté d'une vidéo : `<nom>.<code>.srt` ou `.vtt` (code de 2 ou 3 lettres, variante
« fr-FR » ou « pt-BR » comprise), et `<nom>.srt` (langue inconnue, « ? »).
"""
import json
import os
import re
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import i18n, source

EXTENSIONS_VIDEO = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".mpg", ".mpeg", ".ts", ".wmv", ".flv"}
EXTENSIONS_SOUS_TITRES = {".srt", ".vtt"}
NOM_LOT = "sous-titres.lot.json"
SUFFIXE_ASSEMBLE = ".multi.mkv"                # MKV multipiste créé par l'assemblage : ce n'est pas une vidéo à traiter
_CODE = re.compile(r"^[a-z]{2,3}(?:-[a-zA-Z]{2,4})?$")
_ETIQUETTES = {"eng": "en", "fre": "fr", "fra": "fr", "ger": "de", "deu": "de", "spa": "es", "ita": "it", "por": "pt", "dut": "nl",
               "nld": "nl", "pol": "pl", "rus": "ru", "jpn": "ja", "chi": "zh", "zho": "zh", "kor": "ko", "ara": "ar", "tur": "tr"}


def code_langue_fichier(video_stem: str, fichier: Path) -> str | None:
    """Code de langue d'un fichier de sous-titres voisin de la vidéo, « ? » sans code, None si ce n'est pas le sien."""
    if fichier.suffix.lower() not in EXTENSIONS_SOUS_TITRES:
        return None
    reste = fichier.name[:-len(fichier.suffix)]
    if reste == video_stem:
        return "?"
    if not reste.startswith(video_stem + "."):
        return None
    code = reste[len(video_stem) + 1:]
    return code.split("-")[0].lower() if _CODE.match(code) else None


def langue_piste(video: Path) -> str:
    """Étiquette de langue de la première piste audio (métadonnées du conteneur), code à 2 lettres ou « »."""
    try:
        r = subprocess.run([source.outil("ffprobe"), "-v", "error", "-select_streams", "a:0", "-show_entries", "stream_tags=language",
                            "-of", "json", str(video)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        tag = (json.loads(r.stdout or "{}").get("streams") or [{}])[0].get("tags", {}).get("language", "").lower()
    except (source.ErreurSource, subprocess.SubprocessError, ValueError, OSError):
        return ""
    if not tag or tag == "und":
        return ""
    return _ETIQUETTES.get(tag, tag[:2] if len(tag) == 2 else "")


def inventaire(racine: Path, recursif: bool = True, avec_langue_piste: bool = True) -> dict:
    """Arborescence des vidéos : [{chemin, nom, dossier, taille, sous_titres: [codes], langue_piste}], triée par chemin."""
    racine = Path(racine)
    if not racine.is_dir():
        raise source.ErreurSource(i18n.t("err.folder_not_found", path=racine))
    videos = parcourir(racine, recursif)
    if avec_langue_piste:
        for v in videos:
            v["langue_piste"] = langue_piste(Path(v["chemin"]))
    return {"racine": str(racine).replace("\\", "/"), "videos": videos, "nb": len(videos)}


def _ordre(texte: str) -> list:
    """Clé de tri naturelle : « Season 2 » avant « Season 10 »."""
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", texte)]


def parcourir(racine: Path, recursif: bool = True, annule=lambda: False, progres=lambda _n: None) -> list[dict]:
    """Vidéos du dossier et sous-titres voisins, sans ouvrir les fichiers (rapide) ; `langue_piste` reste à remplir."""
    racine = Path(racine)
    videos = []
    for courant, sous, noms in os.walk(racine):
        if annule():
            break
        sous[:] = [s for s in sous if s != ".travail"] if recursif else []
        ici = Path(courant)
        rel = ici.relative_to(racine)
        for nom in noms:
            p = ici / nom
            if p.suffix.lower() not in EXTENSIONS_VIDEO or nom.lower().endswith(SUFFIXE_ASSEMBLE):
                continue
            try:
                taille = p.stat().st_size
            except OSError:
                continue
            voisins = sorted({c for f in noms if (c := code_langue_fichier(p.stem, Path(f))) is not None})
            videos.append({"chemin": str(p).replace("\\", "/"), "nom": nom, "dossier": "" if rel == Path(".") else str(rel).replace("\\", "/"),
                           "taille": taille, "sous_titres": voisins, "langue_piste": ""})
        progres(len(videos))
    videos.sort(key=lambda v: (_ordre(v["dossier"]), _ordre(v["nom"])))
    return videos


class Analyse:
    """Inventaire en arrière-plan : parcours rapide (la liste s'affiche aussitôt), puis lecture de la langue des pistes
    audio, qui ouvre chaque vidéo. Annulable à tout moment ; annuler pendant les langues garde la liste."""

    def __init__(self, racine: Path, recursif: bool = True, ouvriers: int = 4):
        self.id = uuid.uuid4().hex[:8]
        self.racine = Path(racine)
        self.recursif = recursif
        self.ouvriers = ouvriers
        self.etat = "parcours"                 # parcours, langues, fini, annulee, erreur
        self.vus = 0
        self.faites = 0
        self.videos: list[dict] | None = None
        self.langues: dict[str, str] = {}
        self.erreur = ""
        self._annule = threading.Event()
        self._verrou = threading.Lock()
        fichier_lot = self.racine / NOM_LOT
        self.lot_existant = str(fichier_lot).replace("\\", "/") if fichier_lot.is_file() else None

    def demarrer(self):
        threading.Thread(target=self.executer, daemon=True, name="analyse-dossier").start()

    def annuler(self):
        self._annule.set()

    def _lire_langue(self, v: dict):
        if self._annule.is_set():
            return
        code = langue_piste(Path(v["chemin"]))
        with self._verrou:
            if code:
                self.langues[v["chemin"]] = code
            self.faites += 1

    def executer(self):
        try:
            videos = parcourir(self.racine, self.recursif, self._annule.is_set, lambda n: setattr(self, "vus", n))
            if self._annule.is_set():
                self.etat = "annulee"
                return
            self.vus, self.videos, self.etat = len(videos), videos, "langues"
            with ThreadPoolExecutor(self.ouvriers) as pool:
                list(pool.map(self._lire_langue, videos))
            self.etat = "fini"
        except OSError as e:
            self.etat, self.erreur = "erreur", str(e)

    def vers_dict(self, avec_videos: bool = False) -> dict:
        with self._verrou:
            langues = dict(self.langues)
            faites = self.faites
        d = {"id": self.id, "racine": str(self.racine).replace("\\", "/"), "etat": self.etat, "vus": self.vus, "faites": faites,
             "nb": len(self.videos) if self.videos is not None else 0, "langues": langues, "erreur": self.erreur,
             "langues_interrompues": self._annule.is_set() and self.videos is not None, "lot_existant": self.lot_existant}
        if avec_videos and self.videos is not None:
            d["videos"] = self.videos
        return d


# --- Fichier de lot : sélection, réglages par vidéo, avancement ; relu pour reprendre ---------------------------------

def chemin_lot(racine: Path, sortie: Path | None, dans_sortie: bool) -> Path:
    """Dans le dossier de sortie si la destination « dossier de sortie » est cochée, sinon dans le dossier source."""
    return (Path(sortie) if dans_sortie and sortie else Path(racine)) / NOM_LOT


def ecrire_lot(fichier: Path, lot: dict):
    fichier.parent.mkdir(parents=True, exist_ok=True)
    lot = {**lot, "mis_a_jour": time.strftime("%Y-%m-%d %H:%M:%S")}
    tmp = fichier.with_suffix(".tmp")
    tmp.write_text(json.dumps(lot, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(fichier)


def lire_lot(fichier: Path) -> dict:
    fichier = Path(fichier)
    if fichier.is_dir():
        fichier = fichier / NOM_LOT
    if not fichier.is_file():
        raise source.ErreurSource(i18n.t("err.batch_file_not_found", path=fichier))
    try:
        lot = json.loads(fichier.read_text(encoding="utf-8"))
    except ValueError as e:
        raise source.ErreurSource(i18n.t("err.batch_file_unreadable", error=e)) from e
    if not isinstance(lot.get("videos"), list):
        raise source.ErreurSource(i18n.t("err.batch_file_no_videos"))
    lot["fichier"] = str(fichier).replace("\\", "/")
    return lot
