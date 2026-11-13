"""Serveur de transcription lancé sur le PC à la demande (entrée « transcriptions » avec une `commande`)."""
import atexit
import os
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import i18n

_SANS_FENETRE = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CODE_ECHEC_DEMARRAGE = 3                # uvicorn : l'écoute sur le port a échoué


class ErreurServeurLocal(Exception):
    pass


def _repond(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=2) as r:
            return b"pret" in r.read()
    except OSError:
        return False


def occupant_du_port(url: str) -> str:
    """Programme qui écoute sur le port de `url` : « nom (pid n) », ou "" s'il n'y en a pas ou hors Windows."""
    if os.name != "nt":
        return ""
    port = urllib.parse.urlsplit(url).port
    try:
        lignes = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, errors="replace", timeout=20,
                                creationflags=_SANS_FENETRE).stdout.splitlines()
        pid = next((p[4] for p in map(str.split, lignes) if len(p) == 5 and p[1].endswith(f":{port}") and p[3] == "LISTENING"), None)
        if not pid:
            return ""
        nom = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace", timeout=20,
                             creationflags=_SANS_FENETRE).stdout.split(",")[0].strip().strip('"')
        return f"{nom or i18n.t('state.unknown_program')} (pid {pid})"
    except (OSError, subprocess.SubprocessError):
        return ""


class ServeurLocal:
    """Un seul processus par URL ; `assurer()` le démarre s'il ne répond pas, `arreter()` le coupe s'il vient d'ici."""

    _instances: dict[str, "ServeurLocal"] = {}
    _verrou = threading.Lock()

    def __init__(self, m: dict):
        self.url = m["url"].rstrip("/")
        self.commande = m["commande"]
        self.dossier = m.get("dossier") or None
        self.processus: subprocess.Popen | None = None
        self.journal_fichier = Path(os.environ.get("TEMP", ".")) / "transcription-locale.log"

    @classmethod
    def pour(cls, m: dict) -> "ServeurLocal":
        with cls._verrou:
            if m["url"] not in cls._instances:
                cls._instances[m["url"]] = cls(m)
            return cls._instances[m["url"]]

    def tourne_ici(self) -> bool:
        return self.processus is not None and self.processus.poll() is None

    def etat(self) -> str:
        if _repond(self.url):
            return "pret"
        return "demarrage" if self.tourne_ici() else "arrete"

    def assurer(self, journal=print, attente_s: int = 180) -> bool:
        """Retourne True s'il a fallu le démarrer."""
        if _repond(self.url):
            return False
        if not self.tourne_ici():
            journal(i18n.t("log.local_server_starting", url=self.url))
            sortie = open(self.journal_fichier, "ab")
            self.processus = subprocess.Popen(self.commande, cwd=self.dossier, stdout=sortie, stderr=subprocess.STDOUT,
                                              creationflags=_SANS_FENETRE)
            sortie.close()
            atexit.register(self.arreter)
        debut = time.time()
        while time.time() - debut < attente_s:
            if _repond(self.url):
                journal(i18n.t("log.local_server_ready", s=round(time.time() - debut)))
                return True
            if self.processus and self.processus.poll() is not None:
                code = self.processus.returncode
                occupant = occupant_du_port(self.url) if code == CODE_ECHEC_DEMARRAGE else ""
                detail = i18n.t("err.port_used_by", program=occupant) if occupant else ""
                raise ErreurServeurLocal(i18n.t("err.local_server_stopped", code=code, detail=detail, log=self.journal_fichier))
            time.sleep(1)
        raise ErreurServeurLocal(i18n.t("err.local_server_timeout"))

    def arreter(self):
        if self.tourne_ici():
            p = self.processus
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=20,
                                   creationflags=_SANS_FENETRE)
                else:
                    p.terminate()
                p.wait(timeout=10)
            except (subprocess.SubprocessError, OSError):
                pass
        self.processus = None
