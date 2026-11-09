"""Configuration : valeurs par défaut du paquet (soustitres/config.toml) recouvertes par les réglages de l'utilisateur
(config.json du dossier utilisateur, écrits par l'interface web). Les clés ne sont jamais stockées en clair : une
référence « env:<NOM> », « file:<chemin> » (fichier qui contient la clé) ou « sops:<fichier>#<NOM> » (si sops est installé)."""
import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

FICHIER_DEFAUTS = Path(__file__).resolve().parent / "config.toml"


def dossier_utilisateur() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "sous-titres"


FICHIER_UTILISATEUR = dossier_utilisateur() / "config.json"
REFERENCES = ("env:", "file:", "sops:")
FORMES = "env:<NOM>, file:<chemin> ou sops:<fichier>#<NOM>"


class ErreurConfig(Exception):
    pass


def charger(fichier_defauts: Path = FICHIER_DEFAUTS, fichier_utilisateur: Path | None = FICHIER_UTILISATEUR) -> dict:
    with open(fichier_defauts, "rb") as f:
        cfg = tomllib.load(f)
    if fichier_utilisateur and fichier_utilisateur.exists():
        perso = json.loads(fichier_utilisateur.read_text(encoding="utf-8"))
        cfg = fusionner(cfg, perso)
    if cfg.get("sortie"):
        cfg["sortie"] = os.path.expanduser(cfg["sortie"])
    return cfg


def fusionner(base: dict, dessus: dict) -> dict:
    """Les tables sont fusionnées clé par clé ; les listes (moteurs) et valeurs simples sont remplacées."""
    r = dict(base)
    for k, v in dessus.items():
        if isinstance(v, dict) and isinstance(r.get(k), dict):
            r[k] = fusionner(r[k], v)
        else:
            r[k] = v
    return r


def enregistrer(cfg: dict, fichier: Path = FICHIER_UTILISATEUR):
    for m in cfg.get("transcriptions", []) + cfg.get("traducteurs", []):
        cle = str(m.get("cle", ""))
        if cle and not cle.startswith(REFERENCES):
            raise ErreurConfig(f"clé de « {m.get('nom', m.get('id'))} » : donner une référence {FORMES}, jamais la valeur")
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")


def moteur(cfg: dict, liste: str, actif: str, ident: str | None = None) -> dict:
    """Entrée de cfg[liste] dont l'id est `ident` (ou cfg[actif])."""
    voulu = ident or cfg.get(actif)
    for m in cfg.get(liste, []):
        if m.get("id") == voulu:
            return m
    raise ErreurConfig(f"« {voulu} » absent de {liste}")


def resoudre_cle(reference: str) -> str:
    """Valeur d'une clé à partir de sa référence. Jamais journalisée par les appelants."""
    reference = (reference or "").strip()
    if not reference:
        return ""
    if reference.startswith("env:"):
        return os.environ.get(reference[4:], "")
    if reference.startswith("file:"):
        chemin = Path(os.path.expanduser(reference[5:]))
        try:
            return chemin.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as e:
            raise ErreurConfig(f"fichier de clé illisible : {chemin} ({type(e).__name__})") from e
    if reference.startswith("sops:"):
        fichier, _, nom = reference[5:].partition("#")
        if not nom:
            raise ErreurConfig(f"référence sops sans nom de variable : {reference}")
        sops = shutil.which("sops")
        if not sops:
            raise ErreurConfig("sops introuvable dans le PATH : l'installer, ou utiliser une référence env:<NOM> ou file:<chemin>")
        r = subprocess.run([sops, "-d", "--extract", f'["{nom}"]', fichier], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise ErreurConfig(f"sops n'a pas pu lire {nom} dans {fichier} : {r.stderr.strip()[-200:]}")
        return r.stdout.strip()
    raise ErreurConfig(f"clé en clair refusée : utiliser {FORMES}")
