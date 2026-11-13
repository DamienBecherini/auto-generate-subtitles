"""Serveur HTTP local de l'interface (bibliothèque standard, comme la supervision).

  GET  /                         la page
  GET  /api/sante                contrat « service local » : {etat, contrat, service, version, occupe, activite, reste_travail_s, inactivite}
  GET  /api/etat                 moteurs actifs, état du serveur de transcription local et de l'IA locale, tâche en cours
  GET  /api/config  PUT /api/config      (langue_interface : langue imposée à la page et au journal, vide = automatique)
  GET  /api/langues              langues de l'interface disponibles et réglage     GET /locales/<code>.json   catalogue
  POST /api/taches  {source, transcription?, traducteur?, langue?, langues?, sortie?, destination?, refaire?, pause?}   GET /api/taches
  GET  /api/taches/<id>?depuis=N     POST /api/taches/<id>/annuler     POST /api/taches/<id>/reessayer (tâche en erreur)
  GET  /api/taches/<id>/estimation?langues=fr,en      coût estimé (tâche en pause) pour ces langues
  POST /api/taches/<id>/traduire {traducteur?, langues?}   seconde phase d'une tâche en pause ("aucun" = terminer sans traduire)
  GET  /api/nettoyage            taille des dossiers de travail par catégorie     POST /api/nettoyage {options} → supprimé par catégorie
  POST /api/dossier {chemin}     lance l'inventaire d'un dossier en arrière-plan → {id, etat…}
  GET  /api/dossier/<id>[?videos=1]   avancement, langues des pistes audio, liste des vidéos une fois parcourues
  POST /api/dossier/<id>/annuler
  POST /api/lots {racine, videos: [{chemin, langue}], transcription?, traducteur?, langues?, destination?, sortie?, pause?}
  GET  /api/lots   GET /api/lots/<id>   GET /api/lots/<id>/estimation?langues=
  POST /api/lots/charger {fichier}       relit un sous-titres.lot.json (reprise après coupure) → lot suspendu, à reprendre
  POST /api/lots/<id>/suspendre | annuler | traduire {traducteur?, langues?, arret?} | reprendre {traducteur?, transcription?, langues?, pause?, arret?}
  POST /api/lots/<id>/continuer {chemin}   une vidéo jusqu'au bout de ce que le lot doit produire, avant les autres
  POST /api/lots/<id>/objectif {arret}     traduction (sous-titres seuls) ou assemblage (sous-titres + doublage)
  GET  /api/lots/<id>/relecture?chemin=   noms à vérifier     POST /api/lots/<id>/valider {decisions: [{transcrit, graphie, francais, accepter}]}
  GET  /api/lots/dernier         lot resté inachevé à la session précédente     POST /api/lots/dernier/oublier
  PUT  /api/fichiers?nom=<nom>   corps = le fichier (glisser-déposer) → copié dans le dossier de sortie
  POST /api/transcription-locale/arreter   coupe le serveur de transcription local démarré d'ici
  POST /api/dialogue {type: "fichier"|"dossier", depart?}   boîte de dialogue du système → {chemin}
  POST /api/ouvrir {chemin}      ouvre le dossier (fichier sélectionné) dans l'Explorateur
  POST /api/tester {genre: "transcription"|"traducteur", moteur: {...}}
  POST /api/youtube {url}        titre, durée et fichier de destination, sans télécharger
"""
import shutil
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .. import __version__, config, dossier, etapes, i18n, langue, nettoyage, source, traduction, transcription, transcription_locale
from .taches import Gestionnaire
from .veille import Veille, est_activite

log = logging.getLogger("soustitres.web")
STATIQUE = Path(__file__).resolve().parent / "static"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png"}
_SANS_FENETRE = getattr(subprocess, "CREATE_NO_WINDOW", 0)
gestionnaire = Gestionnaire()
_delai_impose: float | None = None      # --inactivite de la ligne de commande
analyse_courante: dossier.Analyse | None = None   # inventaire du dossier en cours ou affiché (un seul à la fois)


def delai_inactivite_s() -> float:
    if _delai_impose is not None:
        return _delai_impose
    try:
        return float(config.charger().get("web", {}).get("inactivite_min", 60)) * 60
    except (config.ErreurConfig, OSError, ValueError):
        return 0                        # configuration illisible : on ne coupe pas


veille = Veille(delai_inactivite_s, gestionnaire.occupe)


def etat_sante() -> dict:
    """Réponse de GET /api/sante, selon le contrat « service local »."""
    restant = veille.restant_s()
    return {"etat": "pret", "contrat": 1, "service": "sous-titres", "version": __version__,
            "occupe": gestionnaire.occupe(), "activite": gestionnaire.activite(), "reste_travail_s": gestionnaire.reste_s(),
            "inactivite": {"delai_s": delai_inactivite_s(), "restant_s": None if restant is None else round(restant, 1)}}


class ErreurRequete(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def etat_ia_locale(cfg: dict) -> dict | None:
    for m in cfg.get("traducteurs", []):
        if m.get("supervision"):
            try:
                with urllib.request.urlopen(m["supervision"].rstrip("/") + "/api/ia", timeout=5) as r:   # nvidia-smi ≈ 1 s
                    d = json.load(r)
                return {"etat": d.get("etat"), "modeles": [x.get("id") for x in d.get("modeles", [])]}
            except (urllib.error.URLError, OSError, ValueError):
                return {"etat": "inconnu"}
    return None


def etat_transcription_locale(cfg: dict) -> dict:
    r = {}
    for m in cfg.get("transcriptions", []):
        if m.get("commande"):
            r[m["id"]] = transcription_locale.ServeurLocal.pour(m).etat()
    return r


_TITRES = {"fichier": "dialog.pick_video", "lot": "dialog.pick_batch", "dossier_source": "dialog.source_folder"}
_VIDEOS = "*.mp4 *.mkv *.webm *.mov *.avi *.m4a *.mp3 *.opus *.ogg *.wav *.flac"
_MOTIFS = {"fichier": (_VIDEOS, "dialog.videos_audio"), "lot": ("*.lot.json", "dialog.batch_file")}
_SCRIPT_MAC = ("on run argv\n"
               "if (count of argv) > 1 then return POSIX path of (choose {quoi} with prompt (item 1 of argv) "
               "default location ((POSIX file (item 2 of argv)) as alias))\n"
               "return POSIX path of (choose {quoi} with prompt (item 1 of argv))\n"
               "end run")


def commande_dialogue(genre: str, depart: str = "", plateforme: str = sys.platform, trouver=shutil.which) -> list[str] | None:
    """Boîte de dialogue hors Windows : osascript sur macOS, zenity ou kdialog ailleurs ; None si aucun n'est installé."""
    titre, motif = i18n.t(_TITRES.get(genre, "dialog.output_folder")), _MOTIFS.get(genre)
    if motif:
        motif = (motif[0], i18n.t(motif[1]))
    if plateforme == "darwin":
        script = _SCRIPT_MAC.format(quoi="file" if motif else "folder")
        return ["osascript", "-e", script, titre] + ([depart] if depart else [])
    if trouver("zenity"):
        c = ["zenity", "--file-selection", f"--title={titre}"]
        if depart:
            c.append(f"--filename={depart.rstrip('/')}/")
        if motif:
            return c + [f"--file-filter={motif[1]} | {motif[0]}", f"--file-filter={i18n.t('dialog.all_files')} | *"]
        return c + ["--directory"]
    if trouver("kdialog"):
        if motif:
            return ["kdialog", "--title", titre, "--getopenfilename", depart or ".", f"{motif[0]}|{motif[1]}"]
        return ["kdialog", "--title", titre, "--getexistingdirectory", depart or "."]
    return None


def _dialogue_unix(genre: str, depart: str) -> str:
    commande = commande_dialogue(genre, depart)
    if commande is None:
        raise ErreurRequete(501, i18n.t("err.no_dialog"))
    r = subprocess.run(commande, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    chemin = r.stdout.strip() if r.returncode == 0 else ""     # code non nul : choix annulé
    return chemin.rstrip("/") or chemin


def dialogue(genre: str, depart: str = "") -> str:
    """Boîte de dialogue du système, au premier plan (Windows : PowerShell en mode STA) ; chaîne vide si annulée."""
    if os.name != "nt":
        return _dialogue_unix(genre, depart)
    depart = depart.replace("'", "''")
    if genre in ("fichier", "lot"):
        tous = i18n.t("dialog.all_files")
        filtre = (f"{i18n.t('dialog.batch_file')}|*.lot.json|{tous}|*.*" if genre == "lot" else
                  f"{i18n.t('dialog.videos_audio')}|*.mp4;*.mkv;*.webm;*.mov;*.avi;*.m4a;*.mp3;*.opus;*.ogg;*.wav;*.flac|{tous}|*.*")
        filtre = filtre.replace("'", "''")
        corps = ("$d = New-Object System.Windows.Forms.OpenFileDialog; "
                 f"$d.Filter = '{filtre}'; "
                 f"if ('{depart}') {{ $d.InitialDirectory = '{depart}' }}; "
                 "if ($d.ShowDialog($f) -eq 'OK') { $d.FileName }")
    else:
        titre = i18n.t("dialog.source_folder" if genre == "dossier_source" else "dialog.output_folder").replace("'", "''")
        corps = (f"$d = New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description = '{titre}'; "
                 f"if ('{depart}') {{ $d.SelectedPath = '{depart}' }}; "
                 "if ($d.ShowDialog($f) -eq 'OK') { $d.SelectedPath }")
    script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; Add-Type -AssemblyName System.Windows.Forms; "
              "$f = New-Object System.Windows.Forms.Form; $f.TopMost = $true; $f.StartPosition = 'CenterScreen'; " + corps)
    r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600, creationflags=_SANS_FENETRE)
    return r.stdout.strip().replace("\\", "/")


def commande_ouvrir(p: Path, plateforme: str = sys.platform) -> list[str]:
    """Montre un dossier, ou un fichier dans son dossier (sélectionné quand le système le permet)."""
    if plateforme == "win32":
        return ["explorer", str(p)] if p.is_dir() else ["explorer", "/select,", str(p)]
    if plateforme == "darwin":
        return ["open", str(p)] if p.is_dir() else ["open", "-R", str(p)]
    return ["xdg-open", str(p if p.is_dir() else p.parent)]


def ouvrir(chemin: str):
    p = Path(chemin)
    if not p.exists():
        raise ErreurRequete(404, i18n.t("err.not_found_path", path=chemin))
    try:
        subprocess.Popen(commande_ouvrir(p))
    except OSError as e:
        raise ErreurRequete(501, i18n.t("err.cannot_open", error=e)) from e


def tester(genre: str, m: dict) -> dict:
    if genre == "transcription":
        if m.get("commande"):
            transcription_locale.ServeurLocal.pour(m).assurer(lambda _x: None)
        return {"ok": True, "detail": i18n.t("test.responds", state=transcription.sante(m["url"]))}
    if genre == "traducteur":
        if m.get("type") == "aucun":
            return {"ok": True, "detail": i18n.t("test.nothing")}
        return {"ok": True, "detail": i18n.t("test.responds", state=traduction.tester(m))}
    raise ErreurRequete(400, i18n.t("err.unknown_kind"))


class Requete(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "soustitres"

    def parse_request(self):
        ok = super().parse_request()
        if ok:
            langue_page = self.headers.get("X-Langue")
            i18n.pour_requete(langue_page)
            i18n.annonce_page(langue_page)
        if ok and est_activite(self.command, self.path):
            veille.signaler()
        return ok

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/taches/") and self.command == "GET":
            return
        log.info("%s %s", self.command, self.path)

    # --- utilitaires ---
    def _json(self, code: int, donnees):
        corps = json.dumps(donnees, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def _corps_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 5_000_000:
            raise ErreurRequete(413, i18n.t("err.body_too_large"))
        return json.loads(self.rfile.read(n) or b"{}")

    def _fichier(self, chemin: str):
        nom = "index.html" if chemin in ("", "/") else chemin.removeprefix("/static/")
        f = (STATIQUE / nom).resolve()
        if not f.is_file() or STATIQUE not in f.parents:
            return self._json(404, {"erreur": i18n.t("err.not_found")})
        corps = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(f.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(corps)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(corps)

    def _catalogue(self, nom: str):
        """Catalogue de traductions d'une langue (les mêmes fichiers que les messages du serveur)."""
        code = nom.removesuffix(".json")
        if code not in i18n.disponibles():
            return self._json(404, {"erreur": i18n.t("err.not_found")})
        corps = (i18n.DOSSIER / f"{code}.json").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(corps)

    # --- routes ---
    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path == "/" or u.path.startswith("/static/"):
                return self._fichier(u.path)
            if u.path.startswith("/locales/"):
                return self._catalogue(u.path.removeprefix("/locales/"))
            if u.path == "/api/langues":
                reglage = config.charger().get("langue_interface") or ""
                return self._json(200, {"disponibles": i18n.disponibles(), "reglage": i18n.choisir(reglage) or ""})
            if u.path == "/api/sante":
                return self._json(200, etat_sante())
            cfg = config.charger()
            if u.path == "/api/etat":
                courante = gestionnaire.en_cours() or next(iter(reversed(gestionnaire.en_pause())), None)
                en_cours = courante.resume() if courante else None
                lot = gestionnaire.lot_actif()
                return self._json(200, {"lot": lot.resume() if lot else None,
                                        "transcription_active": cfg.get("transcription_active"), "traducteur_actif": cfg.get("traducteur_actif"),
                                        "sortie": cfg.get("sortie"), "langue": cfg.get("langue", ""),
                                        "transcription_locale": etat_transcription_locale(cfg), "ia_locale": etat_ia_locale(cfg),
                                        "tache": en_cours, "config_utilisateur": str(config.FICHIER_UTILISATEUR)})
            if u.path == "/api/config":
                return self._json(200, cfg)
            if u.path == "/api/taches":
                return self._json(200, gestionnaire.liste())
            if u.path == "/api/nettoyage":
                racine = Path(parse_qs(u.query).get("sortie", [""])[0] or cfg["sortie"]) / ".travail"
                return self._json(200, {"racine": str(racine), "categories": nettoyage.bilan(racine), "options": nettoyage.options(cfg)})
            if u.path == "/api/lots":
                return self._json(200, [l.resume() for l in reversed(list(gestionnaire.lots.values()))])
            if u.path == "/api/lots/dernier":
                return self._json(200, {"dernier": gestionnaire.dernier_lot()})
            m = re.fullmatch(r"/api/dossier/([0-9a-f]+)", u.path)
            if m:
                if not analyse_courante or analyse_courante.id != m.group(1):
                    return self._json(404, {"erreur": i18n.t("err.unknown_analysis")})
                return self._json(200, analyse_courante.vers_dict("videos" in parse_qs(u.query)))
            m = re.fullmatch(r"/api/lots/([0-9a-f]+)/relecture", u.path)
            if m:
                r = gestionnaire.propositions_lot(m.group(1), parse_qs(u.query).get("chemin", [None])[0])
                return self._json(200, r) if r is not None else self._json(404, {"erreur": i18n.t("err.unknown_batch")})
            m = re.fullmatch(r"/api/lots/([0-9a-f]+)(/estimation)?", u.path)
            if m:
                lot = gestionnaire.lot(m.group(1))
                if lot is None:
                    return self._json(404, {"erreur": i18n.t("err.unknown_batch")})
                if m.group(2):
                    q = parse_qs(u.query)
                    return self._json(200, gestionnaire.estimer_lot(lot.id, q["langues"][0] if "langues" in q else None) or {})
                return self._json(200, lot.resume())
            m = re.fullmatch(r"/api/taches/([0-9a-f]+)/estimation", u.path)
            if m:
                q = parse_qs(u.query)
                e = gestionnaire.estimer(m.group(1), q["langues"][0] if "langues" in q else None)
                if e is None:
                    return self._json(404, {"erreur": i18n.t("err.task_not_prepared")})
                return self._json(200, e)
            m = re.fullmatch(r"/api/taches/([0-9a-f]+)", u.path)
            if m:
                d = gestionnaire.detail(m.group(1), int(parse_qs(u.query).get("depuis", ["0"])[0]))
                if d is None:
                    return self._json(404, {"erreur": i18n.t("err.unknown_task")})
                return self._json(200, d)
            return self._json(404, {"erreur": i18n.t("err.not_found")})
        except ErreurRequete as e:
            return self._json(e.code, {"erreur": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            if u.path == "/api/taches":
                d = self._corps_json()
                src = (d.get("source") or "").strip().strip('"')
                if not src:
                    raise ErreurRequete(400, i18n.t("err.empty_source"))
                if not source.est_url(src) and Path(src).is_dir():
                    return self._json(409, {"erreur": i18n.t("err.is_folder"), "dossier": True})
                options = {"source": src, "transcription": d.get("transcription") or "", "traducteur": d.get("traducteur") or "",
                           "langue": d.get("langue"), "sortie": (d.get("sortie") or "").strip(), "refaire": bool(d.get("refaire"))}
                if d.get("langues") is not None:
                    options["langues"] = langue.normaliser_codes(d["langues"])
                if isinstance(d.get("destination"), dict):
                    options["destination"] = {k: bool(d["destination"].get(k)) for k in ("a_cote", "sortie")}
                if d.get("pause") is not None:
                    options["pause"] = bool(d["pause"])
                if d.get("jusqua") == "assemblage":
                    options.update(jusqua="assemblage", continuer=True, remplacer=bool(d.get("remplacer")))
                t = gestionnaire.ajouter(options)
                return self._json(201, t.resume())
            m = re.fullmatch(r"/api/taches/([0-9a-f]+)/traduire", u.path)
            if m:
                d = self._corps_json()
                t = gestionnaire.reprendre(m.group(1), d.get("traducteur"), d.get("langues"))
                if t is None:
                    raise ErreurRequete(409, i18n.t("err.task_not_paused"))
                return self._json(200, t.resume())
            if u.path == "/api/dossier":
                d = self._corps_json()
                chemin = Path((d.get("chemin") or "").strip().strip('"'))
                if not chemin.is_dir():
                    raise ErreurRequete(400, i18n.t("err.folder_not_found", path=chemin))
                global analyse_courante
                if analyse_courante:
                    analyse_courante.annuler()
                analyse_courante = dossier.Analyse(chemin, recursif=d.get("recursif", True))
                analyse_courante.demarrer()
                return self._json(202, analyse_courante.vers_dict())
            m = re.fullmatch(r"/api/dossier/([0-9a-f]+)/annuler", u.path)
            if m:
                if analyse_courante and analyse_courante.id == m.group(1):
                    analyse_courante.annuler()
                return self._json(200, {"ok": True})
            if u.path == "/api/lots":
                d = self._corps_json()
                videos = [v for v in d.get("videos") or [] if v.get("chemin")]
                if not videos:
                    raise ErreurRequete(400, i18n.t("err.no_video_selected"))
                options = {k: d.get(k) for k in ("transcription", "traducteur", "langues", "destination", "sortie", "pause", "refaire", "arret", "relecture", "remplacer") if d.get(k) is not None}
                if "langues" in options:
                    options["langues"] = langue.normaliser_codes(options["langues"])
                if options.get("arret") not in (None, *etapes.ARRETS):
                    raise ErreurRequete(400, i18n.t("err.unknown_stop_step"))
                if options.get("arret") == "validation" or options.get("relecture"):
                    options["pause"] = True    # les noms proposés se vérifient avant la traduction, quel que soit le réglage de pause
                return self._json(201, gestionnaire.creer_lot(str(d.get("racine") or Path(videos[0]["chemin"]).parent), videos, options).resume())
            if u.path == "/api/lots/dernier/oublier":
                gestionnaire.oublier_dernier_lot()
                return self._json(200, {"ok": True})
            if u.path == "/api/lots/charger":
                d = self._corps_json()
                try:
                    return self._json(200, gestionnaire.charger_lot((d.get("fichier") or "").strip().strip('"')).resume())
                except source.ErreurSource as e:
                    raise ErreurRequete(400, str(e)) from e
            m = re.fullmatch(r"/api/lots/([0-9a-f]+)/valider", u.path)
            if m:
                r = gestionnaire.valider_noms(m.group(1), self._corps_json().get("decisions") or [])
                if r is None:
                    raise ErreurRequete(404, i18n.t("err.unknown_batch"))
                return self._json(200, {**r, "lot": gestionnaire.lot(m.group(1)).resume()})
            m = re.fullmatch(r"/api/lots/([0-9a-f]+)/(suspendre|reprendre|annuler|traduire|reessayer|continuer|objectif)", u.path)
            if m:
                ident, action = m.groups()
                if action == "traduire":
                    d = self._corps_json()
                    if d.get("arret") not in (None, *etapes.ARRETS):
                        raise ErreurRequete(400, i18n.t("err.unknown_stop_step"))
                    ok = gestionnaire.lot_traduire(ident, d.get("traducteur"), d.get("langues"), d.get("arret"))
                elif action == "objectif":
                    d = self._corps_json()
                    ok = gestionnaire.lot_objectif(ident, d.get("arret") or "", d.get("remplacer"))
                elif action == "continuer":
                    ok = gestionnaire.lot_continuer(ident, self._corps_json().get("chemin") or "")
                elif action == "reessayer":
                    ok = gestionnaire.lot_reessayer(ident, self._corps_json().get("chemin"))
                elif action == "reprendre":
                    ok = gestionnaire.lot_reprendre(ident, self._corps_json())
                else:
                    ok = getattr(gestionnaire, "lot_" + action)(ident)
                if not ok:
                    raise ErreurRequete(409, i18n.t("err.batch_action", action=action))
                return self._json(200, gestionnaire.lot(ident).resume())
            if u.path == "/api/nettoyage":
                d = self._corps_json()
                cfg = config.charger()
                opts = {**nettoyage.options(cfg), **{k: v for k, v in (d.get("options") or {}).items() if k in nettoyage.DEFAUTS}}
                racine = Path(d.get("sortie") or cfg["sortie"]) / ".travail"
                if gestionnaire.en_cours():
                    raise ErreurRequete(409, i18n.t("err.task_running_cleanup"))
                return self._json(200, {"racine": str(racine), "supprime": nettoyage.nettoyer_tout(racine, opts),
                                        "categories": nettoyage.bilan(racine)})
            m = re.fullmatch(r"/api/taches/([0-9a-f]+)/reessayer", u.path)
            if m:
                t = gestionnaire.reessayer(m.group(1))
                return self._json(200, {"id": t.id}) if t else self._json(404, {"erreur": i18n.t("err.task_not_in_error")})
            m = re.fullmatch(r"/api/taches/([0-9a-f]+)/annuler", u.path)
            if m:
                return self._json(200, {"ok": gestionnaire.annuler(m.group(1))})
            if u.path == "/api/transcription-locale/arreter":
                cfg = config.charger()
                for m in cfg.get("transcriptions", []):
                    if m.get("commande"):
                        transcription_locale.ServeurLocal.pour(m).arreter()
                return self._json(200, {"ok": True})
            if u.path == "/api/dialogue":
                d = self._corps_json()
                return self._json(200, {"chemin": dialogue(d.get("type", "fichier"), d.get("depart", ""))})
            if u.path == "/api/ouvrir":
                ouvrir(self._corps_json().get("chemin", ""))
                return self._json(200, {"ok": True})
            if u.path == "/api/tester":
                d = self._corps_json()
                try:
                    return self._json(200, tester(d.get("genre", ""), d.get("moteur") or {}))
                except (transcription.ErreurTranscription, traduction.ErreurTraduction, config.ErreurConfig,
                        transcription_locale.ErreurServeurLocal) as e:
                    return self._json(200, {"ok": False, "detail": str(e)})
            if u.path == "/api/youtube":
                d = self._corps_json()
                cfg = config.charger()
                try:
                    i = source.infos_youtube(d.get("url", ""), cfg.get("youtube", {}).get("commande", ["yt-dlp"]),
                                             Path(d.get("sortie") or cfg["sortie"]))
                except source.ErreurSource as e:
                    return self._json(200, {"ok": False, "detail": str(e)})
                return self._json(200, {"ok": True, "titre": i.get("title"), "duree": i.get("duration"), "video": str(i["video"]),
                                        "existe": i["video"].exists()})
            return self._json(404, {"erreur": i18n.t("err.not_found")})
        except ErreurRequete as e:
            return self._json(e.code, {"erreur": str(e)})
        except (ValueError, KeyError) as e:
            return self._json(400, {"erreur": i18n.t("err.invalid_request", error=e)})

    def do_PUT(self):
        u = urlparse(self.path)
        try:
            if u.path == "/api/config":
                cfg = self._corps_json()
                config.enregistrer(cfg)
                i18n.definir(cfg.get("langue_interface"))
                return self._json(200, config.charger())
            if u.path == "/api/fichiers":
                nom = Path(unquote(parse_qs(u.query).get("nom", [""])[0])).name
                if not nom:
                    raise ErreurRequete(400, i18n.t("err.missing_name"))
                cfg = config.charger()
                dossier = Path(parse_qs(u.query).get("sortie", [""])[0] or cfg["sortie"])
                dossier.mkdir(parents=True, exist_ok=True)
                cible = dossier / nom
                taille = int(self.headers.get("Content-Length") or 0)
                if cible.exists() and cible.stat().st_size == taille:
                    self.rfile.read(taille)       # déjà là, même taille : on ne réécrit pas
                    return self._json(200, {"chemin": str(cible), "existait": True})
                restant = taille
                with open(cible, "wb") as f:
                    while restant > 0:
                        bloc = self.rfile.read(min(1 << 20, restant))
                        if not bloc:
                            break
                        f.write(bloc)
                        restant -= len(bloc)
                if restant:
                    cible.unlink(missing_ok=True)
                    raise ErreurRequete(400, i18n.t("err.incomplete_upload"))
                return self._json(200, {"chemin": str(cible), "existait": False})
            return self._json(404, {"erreur": i18n.t("err.not_found")})
        except ErreurRequete as e:
            return self._json(e.code, {"erreur": str(e)})
        except config.ErreurConfig as e:
            return self._json(400, {"erreur": str(e)})
        except (ValueError, KeyError) as e:
            return self._json(400, {"erreur": i18n.t("err.invalid_request", error=e)})


def _surveiller_inactivite(serveur):
    while True:
        time.sleep(1)
        if veille.expiree():
            log.info(i18n.t("srv.idle_stop", min=f"{veille.delai_s() / 60:.0f}"))
            serveur.shutdown()
            return


def servir(port: int, inactivite_min: float | None = None):
    global _delai_impose
    if inactivite_min is not None:
        _delai_impose = inactivite_min * 60
    veille.signaler()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    serveur = ThreadingHTTPServer(("127.0.0.1", port), Requete)
    serveur.daemon_threads = True
    try:
        cfg = config.charger()
        i18n.definir(cfg.get("langue_interface"))
        nb, octets = nettoyage.purger_journaux(Path(cfg["sortie"]) / ".travail", float(nettoyage.options(cfg)["journaux_age_h"]))
        if nb:
            log.info(i18n.t("srv.logs_purged", n=nb, mb=f"{octets / 1e6:.1f}"))
    except (config.ErreurConfig, OSError, ValueError, KeyError) as e:
        log.warning(i18n.t("srv.purge_failed", error=e))
    log.info(i18n.t("srv.listening", url=f"http://127.0.0.1:{port}/"))
    threading.Thread(target=_surveiller_inactivite, args=(serveur,), daemon=True, name="veille").start()
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        gestionnaire.fermer()
        for m in config.charger().get("transcriptions", []):
            if m.get("commande"):
                transcription_locale.ServeurLocal.pour(m).arreter()
