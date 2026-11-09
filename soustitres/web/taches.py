"""File des tâches de sous-titrage : une à la fois (le serveur de transcription et l'IA locale n'en traitent qu'une),
sauf la traduction d'un lot par une API distante, qui avance sur plusieurs vidéos (`traduction.simultanees`).

Deux phases (voir pipeline) : préparation (source, audio, transcription, sous-titres d'origine), puis traduction et
copies. Avec l'option `pause`, la tâche s'arrête entre les deux (état `en_pause`) avec une estimation du coût ;
`reprendre()` relance la seconde phase, éventuellement avec un autre traducteur ou d'autres langues.
Historique (50 dernières tâches, résumés) dans %LOCALAPPDATA%/sous-titres/historique.json ; le journal complet
de chaque tâche est écrit à sa fin dans le dossier de travail de la vidéo (.travail/<nom>/journal-<date>-<id>.txt)
et relu de là pour les tâches des sessions précédentes."""
import json
import subprocess
import threading
import time
import traceback
import uuid
from pathlib import Path

from .. import config, dossier, doublage, estimation, etapes, glossaire, langue, pipeline, relecture, source, traduction, transcription, transcription_locale
from . import temps
from .lots import Lot

FICHIER_HISTORIQUE = config.dossier_utilisateur() / "historique.json"
FICHIER_DERNIER_LOT = config.dossier_utilisateur() / "dernier_lot.json"   # chemin du fichier du dernier lot non terminé
ERREURS_CONNUES = (source.ErreurSource, transcription.ErreurTranscription, traduction.ErreurTraduction,
                   config.ErreurConfig, transcription_locale.ErreurServeurLocal, doublage.ErreurDoublage)
ETATS_ACTIFS = ("en_attente", "en_cours")
ETATS_FINIS = ("terminee", "erreur", "annulee")


class Tache:
    def __init__(self, options: dict):
        self.id = uuid.uuid4().hex[:8]
        self.options = options                 # source, transcription, traducteur, langue, langues, sortie, destination, refaire, pause
        self.etat = "en_attente"               # en_attente, en_cours, en_pause, terminee, erreur, annulee
        self.etape = ""
        self.journal: list[str] = []
        self.resultat: dict | None = None
        self.erreur = ""
        self.creee = time.time()
        self.debut = None
        self.fin = None
        self.annulation = False
        self.travail: Path | None = None       # dossier .travail/<nom>, connu après l'étape source
        self.journal_fichier: str | None = None
        self.prepare: pipeline.Prepare | None = None   # première phase faite
        self.estimation: dict | None = None
        self.duree_video = float(options.get("duree_video") or 0)   # connue d'avance dans un lot, sinon à l'extraction de l'audio
        self.passe, self.passe_debut = 1, None  # voir temps.py
        self.glossaire_vu = 0.0                # état du glossaire au moment de la découpe

    def ecrire(self, msg: str):
        self.journal.append(time.strftime("[%H:%M:%S] ") + msg)

    def poids(self) -> float:
        if self.passe in (1, 3):
            return self.duree_video
        return len(self.prepare.sous_titres) * max(1, len(self.options.get("langues") or [])) if self.prepare else 0.0

    def reste_s(self, maintenant: float | None = None) -> int | None:
        """Secondes avant la fin de cette passe ; None si la tâche ne travaille pas ou si rien ne permet d'estimer."""
        if self.etat != "en_cours" or self.passe_debut is None or self.poids() <= 0:
            return None
        taux = temps.taux_connu(self.passe, self.options)
        if not taux:
            return None
        return round(max(0.0, self.poids() * taux - ((time.time() if maintenant is None else maintenant) - self.passe_debut)))

    def resume(self) -> dict:
        return {"id": self.id, "source": self.options.get("source", ""), "etat": self.etat, "etape": self.etape,
                "creee": self.creee, "debut": self.debut, "fin": self.fin, "erreur": self.erreur, "resultat": self.resultat,
                "journal_fichier": self.journal_fichier, "estimation": self.estimation, "reste_s": self.reste_s(), "passe_debut": self.passe_debut,
                "prepare": {"video": str(self.prepare.video), "langue": self.prepare.code, "nb": len(self.prepare.sous_titres),
                            "srt_origine": str(self.prepare.srt_origine)} if self.prepare else None,
                "options": {k: v for k, v in self.options.items() if k != "source"}}

    def detail(self, depuis: int = 0) -> dict:
        d = self.resume()
        d["journal"] = self.journal[depuis:]
        d["journal_total"] = len(self.journal)
        return d


class Gestionnaire:
    def __init__(self):
        self.taches: dict[str, Tache] = {}
        self.ordre: list[str] = []
        self.lots: dict[str, Lot] = {}
        self.verrou = threading.Lock()
        self.reveil = threading.Event()
        self.historique = self._charger_historique()
        threading.Thread(target=self._boucle, daemon=True, name="taches").start()

    # --- API ---
    def ajouter(self, options: dict) -> Tache:
        t = Tache(options)
        with self.verrou:
            self.taches[t.id] = t
            self.ordre.append(t.id)
        t.ecrire(f"En attente : {options.get('source')}")
        self.reveil.set()
        return t

    def obtenir(self, ident: str) -> Tache | None:
        return self.taches.get(ident)

    def detail(self, ident: str, depuis: int = 0) -> dict | None:
        """Tâche en mémoire, ou tâche archivée (résumé de l'historique + journal relu depuis son fichier)."""
        t = self.taches.get(ident)
        if t:
            return t.detail(depuis)
        h = next((x for x in self.historique if x["id"] == ident), None)
        if not h:
            return None
        lignes = []
        if h.get("journal_fichier"):
            try:
                lignes = Path(h["journal_fichier"]).read_text(encoding="utf-8").splitlines()
            except OSError:
                lignes = [f"(journal introuvable : {h['journal_fichier']})"]
        return {**h, "journal": lignes[depuis:], "journal_total": len(lignes)}

    def annuler(self, ident: str) -> bool:
        t = self.taches.get(ident)
        if not t or t.etat not in ETATS_ACTIFS + ("en_pause",):
            return False
        t.annulation = True
        if t.etat in ("en_attente", "en_pause"):
            t.etat, t.fin = "annulee", time.time()
            t.ecrire("Annulée avant de commencer." if t.prepare is None else
                     "Annulée en pause : pas de traduction ; sous-titres d'origine laissés dans le dossier de travail.")
            self._ecrire_journal(t)
            self._archiver(t)
            if t.options.get("lot"):
                self._maj_lot(t)
        else:
            t.ecrire("Annulation demandée : arrêt à la fin de l'étape en cours.")
        return True

    def reprendre(self, ident: str, traducteur: str | None = None, langues: list[str] | None = None) -> Tache | None:
        """Seconde phase d'une tâche en pause, avec éventuellement un autre traducteur ou d'autres langues."""
        t = self.taches.get(ident)
        if not t or t.etat != "en_pause":
            return None
        if traducteur is not None:
            t.options["traducteur"] = traducteur
        if langues is not None:
            t.options["langues"] = langue.normaliser_codes(langues)
        t.etat = "en_attente"
        t.ecrire(f"Reprise : traduction par « {t.options.get('traducteur') or 'traducteur actif'} » vers {', '.join(t.options.get('langues') or []) or 'les langues par défaut'}.")
        self.reveil.set()
        return t

    def estimer(self, ident: str, langues: list[str] | None = None) -> dict | None:
        t = self.taches.get(ident)
        if not t or not t.prepare:
            return None
        cfg = config.charger()
        voulues = langue.normaliser_codes(langues) if langues is not None else pipeline.langues_cibles(self._demande(t, cfg))
        restantes = pipeline.langues_restantes(t.prepare, voulues, bool(t.options.get("refaire")))
        return estimation.estimer(cfg, len(t.prepare.sous_titres), restantes, t.prepare.code)

    def en_cours(self) -> Tache | None:
        return next((t for t in self.taches.values() if t.etat in ETATS_ACTIFS), None)

    def occupe(self) -> bool:
        """Une tâche tourne ou attend son tour, ou un lot enchaîne ses vidéos."""
        return self.en_cours() is not None or any(l.etat == "en_cours" for l in self.lots.values())

    def activite(self) -> str:
        """Ce qui avance, en quelques mots (contrat « service local ») : « lot 3/22 » ou l'étape de la tâche ; vide si rien."""
        lot = next((l for l in list(self.lots.values()) if l.etat == "en_cours"), None)
        if lot:
            return lot.avancement()
        t = self.en_cours()
        return (t.etape or "tâche en cours") if t else ""

    def reste_s(self) -> int | None:
        """Secondes avant la fin du travail en cours (lot, sinon tâche seule), si on peut l'estimer."""
        lot = next((l for l in list(self.lots.values()) if l.etat == "en_cours"), None)
        if lot:
            return lot.reste_s(temps.taux_connu(lot.phase, lot.options))
        t = self.en_cours()
        return t.reste_s() if t else None

    def fermer(self):
        """À l'arrêt du service : les tâches isolées en pause sont archivées avec leur journal. Celles d'un lot restent
        décrites par le fichier de lot, d'où la reprise. Dans les deux cas transcription.json évite de retranscrire."""
        for t in self.en_pause():
            if not t.options.get("lot"):
                t.ecrire("Service arrêté pendant la pause : relancer la vidéo reprend après la transcription, déjà faite.")
                t.etat, t.fin = "annulee", time.time()
                self._ecrire_journal(t)
                self._archiver(t)

    def en_pause(self) -> list[Tache]:
        return [self.taches[i] for i in self.ordre if self.taches[i].etat == "en_pause"]

    def liste(self) -> list[dict]:
        courantes = [self.taches[i].resume() for i in reversed(self.ordre)]
        vus = {t["id"] for t in courantes}
        return courantes + [h for h in self.historique if h["id"] not in vus]

    # --- Exécution ---
    def _boucle(self):
        while True:
            self.reveil.wait(timeout=5)
            self.reveil.clear()
            for ident in list(self.ordre):
                t = self.taches[ident]
                if t.etat == "en_attente":
                    self._demarrer(t)
            self._alimenter_lots()

    def _demarrer(self, t: Tache):
        """Une tâche à la fois, sauf les traductions d'un lot par une API distante : chacune dans son fil."""
        lot = self.lots.get(t.options.get("lot") or "")
        if lot and lot.simultanees > 1 and t.prepare is not None and not etapes.va_jusqua(t.options.get("jusqua"), "doublage"):   # une seule carte graphique
            t.etat = "en_cours"                                # avant le fil : la boucle ne doit pas la relancer
            threading.Thread(target=self._executer, args=(t,), daemon=True, name=f"tache-{t.id}").start()
        else:
            self._executer(t)

    # --- Lots (dossier source) ---
    def creer_lot(self, racine: str, videos: list[dict], options: dict) -> Lot:
        cfg = config.charger()
        dest = {**pipeline.DESTINATION_DEFAUT, **(cfg.get("destination") or {}), **(options.get("destination") or {})}
        fichier = dossier.chemin_lot(Path(racine), Path(options.get("sortie") or cfg["sortie"]), bool(dest.get("sortie")))
        liste = [{"chemin": v["chemin"], "nom": Path(v["chemin"]).name, "langue": v.get("langue") or "", "etat": "a_faire",
                  "tache": None, "nb": None, "langue_detectee": "", "erreur": ""} for v in videos]
        lot = Lot(racine, liste, options, fichier)
        lot.apres_enregistrement = self._noter_dernier_lot
        with self.verrou:
            self.lots[lot.id] = lot
        self._reperer_transcrites(lot)
        lot.enregistrer()
        self._mesurer_durees(lot)
        self.reveil.set()
        return lot

    @staticmethod
    def _reperer_transcrites(lot: Lot):
        """Vidéos dont la transcription est déjà là : `Lot.prochaine` les passe avant celles à transcrire."""
        sortie = lot.options.get("sortie") or config.charger()["sortie"]
        for v in lot.videos:
            v["transcrite"] = not lot.options.get("refaire") and pipeline.transcription_conservee(sortie, v["chemin"])
        Gestionnaire._constater(lot)

    @staticmethod
    def _constater(lot: Lot, videos: list[dict] | None = None):
        """Étapes faites par vidéo, lues sur le disque : pastilles de la liste et choix de « Continuer »."""
        cfg = config.charger()
        sortie = lot.options.get("sortie") or cfg["sortie"]
        langues = langue.normaliser_codes(lot.options.get("langues") or cfg.get("langues_cibles", ["fr"]))
        attente = relecture.noms_en_attente(lot.fichier)
        for v in videos if videos is not None else lot.videos:
            faites = etapes.constater(pipeline.dossier_travail(sortie, v["chemin"]), v["chemin"], langues, v.get("langue_detectee") or v.get("langue") or "", attente)
            faites.pop("transcrite")           # calculé par _reperer_transcrites (tient compte de « refaire ») ou à la fin de la tâche
            v.update(faites)

    @staticmethod
    def _mesurer_durees(lot: Lot):
        """Durée de chaque vidéo, en arrière-plan : le temps restant tient compte de celles qui sont plus longues."""
        def mesurer():
            for v in lot.videos:
                if not v.get("duree"):
                    try:
                        v["duree"] = source.duree(Path(v["chemin"]))
                    except (source.ErreurSource, OSError, subprocess.SubprocessError):
                        pass
        threading.Thread(target=mesurer, daemon=True, name="durees-lot").start()

    def charger_lot(self, fichier: str) -> Lot:
        lot = Lot.depuis_fichier(Path(fichier))
        lot.apres_enregistrement = self._noter_dernier_lot
        for autre in list(self.lots.values()):                # un seul lot par fichier
            if autre.fichier == lot.fichier and autre.etat not in ("termine", "annule"):
                self.lot_annuler(autre.id)                    # hors du verrou : annuler une tâche l'archive, qui le prend
        with self.verrou:
            self.lots[lot.id] = lot
        self._reperer_transcrites(lot)
        lot.enregistrer()
        self._mesurer_durees(lot)
        return lot

    # --- Dernier lot : proposé à la reprise au début de la session suivante ---
    def _noter_dernier_lot(self, lot: Lot):
        try:
            if lot.etat not in ("termine", "annule"):
                FICHIER_DERNIER_LOT.parent.mkdir(parents=True, exist_ok=True)
                FICHIER_DERNIER_LOT.write_text(json.dumps({"fichier": str(lot.fichier)}, ensure_ascii=False), encoding="utf-8")
            elif self._fichier_dernier_lot() == str(lot.fichier):
                FICHIER_DERNIER_LOT.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _fichier_dernier_lot() -> str:
        try:
            return json.loads(FICHIER_DERNIER_LOT.read_text(encoding="utf-8")).get("fichier", "")
        except (OSError, ValueError):
            return ""

    def dernier_lot(self) -> dict | None:
        """Lot resté inachevé à la fin de la session précédente, ou None (terminé, déjà ouvert, ou fichier disparu)."""
        fichier = self._fichier_dernier_lot()
        if not fichier:
            return None
        try:
            d = dossier.lire_lot(Path(fichier))
        except source.ErreurSource:
            return None
        if d.get("etat") in ("termine", "annule") or d.get("id") in self.lots or all(v.get("etat") == "fait" for v in d["videos"]):
            return None
        return {"fichier": d["fichier"], "racine": d.get("racine", ""), "etat": d.get("etat", ""), "mis_a_jour": d.get("mis_a_jour", ""),
                "total": len(d["videos"]), "faites": sum(1 for v in d["videos"] if v.get("etat") == "fait"),
                "traducteur": (d.get("options") or {}).get("traducteur", "")}

    def oublier_dernier_lot(self):
        """« Ignorer » : le lot n'est plus proposé ; son fichier reste, pour « Reprendre un lot… »."""
        FICHIER_DERNIER_LOT.unlink(missing_ok=True)

    def lot(self, ident: str) -> Lot | None:
        return self.lots.get(ident)

    def lot_actif(self) -> Lot | None:
        return next((l for l in reversed(list(self.lots.values())) if l.etat not in ("termine", "annule")), None)

    def lot_suspendre(self, ident: str) -> bool:
        lot = self.lots.get(ident)
        if not lot or lot.etat != "en_cours":
            return False
        lot.etat = "suspendu"                                  # la vidéo en cours va jusqu'à sa fin (ou sa pause)
        lot.enregistrer()
        return True

    def lot_reprendre(self, ident: str, modifs: dict | None = None) -> bool:
        """Relance un lot en pause ; `modifs` (traducteur, transcription, langues, pause) vaut pour les vidéos pas encore lancées."""
        lot = self.lots.get(ident)
        if not lot or lot.etat != "suspendu":
            return False
        modifs = modifs or {}
        for cle in ("traducteur", "transcription"):
            if modifs.get(cle):
                lot.options[cle] = modifs[cle]
        if modifs.get("langues") is not None:
            lot.options["langues"] = langue.normaliser_codes(modifs["langues"])
        if modifs.get("pause") is not None:
            lot.options["pause"] = bool(modifs["pause"])
        if modifs.get("arret") in etapes.ARRETS:
            lot.options["arret"] = modifs["arret"]
        lot.etat = "en_cours"
        lot.enregistrer()
        self.reveil.set()
        return True

    def lot_traduire(self, ident: str, traducteur: str | None, langues: list[str] | None, arret: str | None = None) -> bool:
        lot = self.lots.get(ident)
        if not lot or lot.etat != "attente_traduction":
            return False
        if traducteur is not None:
            lot.options["traducteur"] = traducteur
        if langues is not None:
            lot.options["langues"] = langue.normaliser_codes(langues)
        if arret:
            lot.options["arret"] = arret
        lot.phase, lot.etat = 2, "en_cours"
        lot.enregistrer()
        self.reveil.set()
        return True

    def lot_annuler(self, ident: str) -> bool:
        lot = self.lots.get(ident)
        if not lot or lot.etat in ("termine", "annule"):
            return False
        lot.etat, lot.fin = "annule", time.time()
        for v in lot.videos:
            if v.get("tache") and v["etat"] in ("en_cours", "prepare"):
                self.annuler(v["tache"])
            if v["etat"] in ("a_faire", "prepare", "en_cours"):
                v["etat"] = "annule" if v["etat"] != "a_faire" else "a_faire"
        lot.enregistrer()
        return True

    def lot_reessayer(self, ident: str, chemin: str | None = None) -> bool:
        """Remet à faire une vidéo en erreur (ou toutes) ; un lot terminé ou en attente de traduction repart pour elle."""
        lot = self.lots.get(ident)
        if not lot or lot.etat == "annule":
            return False
        cibles = [v for v in lot.videos if v["etat"] == "erreur" and chemin in (None, v["chemin"])]
        if not cibles:
            return False
        for v in cibles:
            v["etat"], v["tache"], v["erreur"] = "a_faire", None, ""
        if lot.etat in ("termine", "attente_traduction"):
            lot.etat, lot.fin = "en_cours", None
        lot.enregistrer()
        self.reveil.set()
        return True

    def lot_continuer(self, ident: str, chemin: str) -> bool:
        """Fait avancer une seule vidéo jusqu'au bout de la chaîne, avant les autres. Fonctionne aussi sur un lot en pause ou terminé."""
        lot = self.lots.get(ident)
        if not lot or lot.etat == "annule":
            return False
        veut_doublage = etapes.va_jusqua(lot.options.get("arret"), "doublage") and doublage.disponible(config.charger())
        jusqua = lot.options["arret"] if veut_doublage else "traduction"
        with lot.verrou:
            v = next((x for x in lot.videos if x["chemin"] == chemin), None)
            if v is None or v["etat"] == "en_cours":
                return False
            t = self.taches.get(v.get("tache") or "")
            if v["etat"] == "prepare" and t and t.etat == "en_pause":
                t.options.update(jusqua=jusqua, continuer=True, pause=False)
                self.reprendre(t.id, lot.options.get("traducteur"), lot.options.get("langues"))
            else:
                t = self._tache_de_lot(lot, v, jusqua=jusqua, pause=False, continuer=True, verifier=True)
            v["tache"], v["etat"], v["debut"], v["erreur"] = t.id, "en_cours", time.time(), ""
            if lot.etat == "termine":          # repart le temps de cette vidéo ; la page la suit comme le reste du lot
                lot.etat, lot.fin, lot.phase = "en_cours", None, 3
            lot.enregistrer()
        self.reveil.set()
        return True

    def lot_objectif(self, ident: str, arret: str, remplacer: bool | None = None) -> bool:
        """Change ce que le lot doit produire. Doublage demandé après coup : les vidéos déjà sous-titrées redeviennent à doubler,
        mais rien ne part tout seul sur un lot en pause ou terminé (« Reprendre » ou « Continuer » épisode par épisode)."""
        lot = self.lots.get(ident)
        if not lot or lot.etat == "annule" or arret not in etapes.ARRETS:
            return False
        double = etapes.va_jusqua(arret, "doublage") and doublage.disponible(config.charger())
        fini = "assemblee" if arret == "assemblage" else "doublee"
        with lot.verrou:
            lot.options["arret"] = arret
            if remplacer is not None:
                lot.options["remplacer"] = bool(remplacer) and double
            for v in lot.videos:
                if double and v["etat"] == "fait" and v.get("traduite") and not v.get(fini):
                    v["etat"] = "traduit"
                elif not double and v["etat"] == "traduit":
                    v["etat"] = "fait"
            if lot.etat == "termine" and any(v["etat"] == "traduit" for v in lot.videos):
                lot.etat, lot.fin = "suspendu", None
            lot.enregistrer()
        self.reveil.set()
        return True

    def propositions_lot(self, ident: str, chemin: str | None = None) -> dict | None:
        """Noms à vérifier : ceux d'une vidéo, ou tous ceux du lot."""
        lot = self.lots.get(ident)
        if not lot:
            return None
        noms = None
        if chemin:
            f = pipeline.dossier_travail(lot.options.get("sortie") or config.charger()["sortie"], chemin) / relecture.FICHIER_VIDEO
            try:
                noms = set(json.loads(f.read_text(encoding="utf-8")).get("noms", []))
            except (OSError, ValueError):
                noms = set()
        return {"propositions": relecture.propositions(lot.fichier, noms), "chemin": chemin or ""}

    def valider_noms(self, ident: str, decisions: list[dict]) -> dict | None:
        lot = self.lots.get(ident)
        if not lot:
            return None
        acceptes, ecartes = relecture.valider(lot.fichier, decisions)
        with lot.verrou:
            self._constater(lot)
            lot.enregistrer()
        return {"acceptes": acceptes, "ecartes": ecartes}

    def estimer_lot(self, ident: str, langues: list[str] | None = None) -> dict | None:
        """Somme des estimations des vidéos préparées (phase 1 faite, tâche en pause)."""
        lot = self.lots.get(ident)
        if not lot:
            return None
        total = None
        for v in lot.videos:
            if v["etat"] != "prepare" or not v.get("tache"):
                continue
            e = self.estimer(v["tache"], langues if langues is not None else lot.options.get("langues"))
            if not e or not e["langues"]:              # déjà traduite dans ces langues : ne coûtera rien
                continue
            if total is None:
                total = {**e, "videos": 0, "traducteurs": [dict(t) for t in e["traducteurs"]]}
            else:
                total["langues"] += [c for c in e["langues"] if c not in total["langues"]]
                for k in ("nb", "requetes", "jetons_entree", "jetons_sortie"):
                    total[k] += e[k]
                for a, b in zip(total["traducteurs"], e["traducteurs"]):
                    for k in ("cout_usd", "duree_s"):
                        if a.get(k) is not None and b.get(k) is not None:
                            a[k] = round(a[k] + b[k], 4)
            total["videos"] += 1
        return total or {"langues": [], "nb": 0}

    def _alimenter_lots(self):
        """Lance les vidéos suivantes de chaque lot en cours, jusqu'au nombre permis en même temps."""
        for lot in list(self.lots.values()):
            if lot.etat == "en_cours":
                with lot.verrou:
                    self._alimenter_lot(lot)

    def _simultanees(self, lot: Lot) -> int:
        if lot.phase != 2:
            return 1
        try:
            return traduction.simultanees(config.moteur(config.charger(), "traducteurs", "traducteur_actif", lot.options.get("traducteur") or None))
        except config.ErreurConfig:
            return 1

    def _alimenter_lot(self, lot: Lot):
        lot.simultanees = self._simultanees(lot)
        def active(v):
            t = self.taches.get(v.get("tache") or "")
            return v["etat"] == "en_cours" and t is not None and t.etat in ETATS_ACTIFS
        actives = sum(1 for v in lot.videos if active(v))
        for _ in range(lot.simultanees - actives):
            v = lot.prochaine()
            if v is None:
                if actives == 0:               # les vidéos parallèles encore en route finiront la phase
                    lot.terminer_phase()
                    lot.enregistrer()
                return
            o = lot.options
            if v["etat"] == "a_faire":
                t = self._tache_de_lot(lot, v, pause=lot.phase == 1 and bool(o.get("pause", True)))
                v["tache"], v["etat"] = t.id, "en_cours"
                v["debut"] = time.time()
            elif v["etat"] == "traduit":
                t = self._tache_de_lot(lot, v, pause=False, jusqua=o.get("arret"), relecture=False)
                v["tache"], v["etat"] = t.id, "en_cours"
                v["debut"] = time.time()
            else:
                if self.reprendre(v["tache"], o.get("traducteur"), o.get("langues")) is None:
                    v["etat"], v["erreur"] = "erreur", "tâche en pause introuvable (interface redémarrée ?) : reprendre le lot"
                    continue
                v["etat"], v["debut"] = "en_cours", time.time()
            actives += 1
            lot.enregistrer()

    def _tache_de_lot(self, lot: Lot, v: dict, **options) -> Tache:
        o = lot.options
        return self.ajouter({"source": v["chemin"], "transcription": o.get("transcription") or "", "traducteur": o.get("traducteur") or "",
                             "langue": v.get("langue") or "", "langues": o.get("langues"), "sortie": o.get("sortie") or "",
                             "destination": o.get("destination"), "refaire": bool(o.get("refaire")) and v["etat"] == "a_faire", "lot": lot.id,
                             "duree_video": v.get("duree") or 0, "glossaire": str(glossaire.chemin(lot.fichier)), "fichier_lot": str(lot.fichier),
                             "relecture": bool(o.get("relecture")), "pause": False, **options})

    def _reste_a_doubler(self, lot: Lot, t: Tache) -> bool:
        """Traduite, et le lot demande d'aller plus loin : la vidéo attend sa place à la carte graphique (phase 3)."""
        try:
            possible = doublage.disponible(config.charger())
        except config.ErreurConfig:
            possible = False
        return possible and etapes.va_jusqua(lot.options.get("arret"), "doublage") and not etapes.va_jusqua(t.options.get("jusqua"), "doublage")

    def _maj_lot(self, t: Tache):
        lot = self.lots.get(t.options.get("lot") or "")
        v = lot.video_de(t.id) if lot else None
        if not v:
            return
        with lot.verrou:
            if t.etat == "en_pause":
                v["etat"] = "prepare"
                if t.prepare:
                    v["nb"], v["langue_detectee"] = len(t.prepare.sous_titres), t.prepare.code
            elif t.etat == "terminee":
                v["etat"] = "traduit" if self._reste_a_doubler(lot, t) else "fait"
                if t.prepare:
                    v["nb"], v["langue_detectee"] = len(t.prepare.sous_titres), t.prepare.code
            elif t.etat == "erreur":
                v["etat"], v["erreur"] = "erreur", t.erreur
            elif t.etat == "annulee":
                v["etat"] = "annule"
            if lot.etat == "attente_traduction" and not any(x["etat"] == "prepare" for x in lot.videos):
                lot.terminer_phase()           # la dernière vidéo en attente a été traduite ou annulée individuellement
            if t.prepare:
                v["transcrite"] = True
                if (t.resultat or {}).get("remplace"):      # la vidéo a changé de fichier
                    v["chemin"], v["nom"] = t.resultat["mkv"].replace("\\", "/"), Path(t.resultat["mkv"]).name
                self._constater(lot, [v])
            lot.enregistrer()
        self.reveil.set()

    def _demande(self, t: Tache, cfg: dict) -> pipeline.Demande:
        o = t.options
        return pipeline.Demande(o["source"], cfg, o.get("transcription") or None, o.get("traducteur") or None,
                                o.get("langue") if o.get("langue") is not None else None, o.get("sortie") or None,
                                bool(o.get("refaire")), sur_travail=lambda chemin: setattr(t, "travail", chemin),
                                langues=o.get("langues"), destination=o.get("destination"),
                                sur_duree=lambda s: setattr(t, "duree_video", t.duree_video or s), glossaire=o.get("glossaire"))

    def _executer(self, t: Tache):
        t.etat = "en_cours"
        if t.debut is None:
            t.debut = time.time()
        o = t.options
        jusqua = o.get("jusqua") or "traduction"
        t.passe, t.passe_debut = 3 if etapes.va_jusqua(jusqua, "doublage") else 2 if t.prepare is not None else 1, time.time()
        try:
            cfg = config.charger()
            d = self._demande(t, cfg)
            etape = lambda nom: setattr(t, "etape", nom)  # noqa: E731
            annule = lambda: t.annulation  # noqa: E731
            premiere = t.prepare is None
            if premiere or glossaire.empreinte(o.get("glossaire")) != t.glossaire_vu:      # noms validés depuis : découpe à refaire
                t.glossaire_vu = glossaire.empreinte(o.get("glossaire"))
                t.prepare = pipeline.preparer(d, t.ecrire, etape, annule)
            if premiere:
                en_attente = self._relire(t, d, cfg, etape)
                restantes = pipeline.langues_restantes(t.prepare, pipeline.langues_cibles(d), d.refaire)
                t.estimation = estimation.estimer(cfg, len(t.prepare.sous_titres), restantes, t.prepare.code)
                t.ecrire("Estimation de la traduction :\n" + estimation.texte(t.estimation, cfg))
                m_trad = config.moteur(cfg, "traducteurs", "traducteur_actif", d.traducteur_id)
                if o.get("verifier") and en_attente:
                    t.etat, t.etape = "en_pause", "relecture"
                    t.ecrire(f"En pause : {len(en_attente)} nom(s) à vérifier avant de continuer cet épisode.")
                    return
                pause = o.get("pause") if o.get("pause") is not None else estimation.pause_par_defaut(m_trad)
                if pause and t.estimation["langues"] and m_trad.get("type", "aucun") != "aucun":
                    t.etat, t.etape = "en_pause", "traduction"
                    t.ecrire("En pause : choisir le traducteur et les langues, puis « Traduire » (ou « Terminer sans traduire »).")
                    return
            r = pipeline.traduire_et_copier(d, t.prepare, t.ecrire, etape, annule)
            t.resultat = {"video": str(r.video), "srt": str(r.srt), "srt_origine": str(r.srt_origine), "langue": r.langue,
                          "nb": r.nb_sous_titres, "traduit": r.traduit, "copies": [str(c) for c in r.copies],
                          "duree_s": round(r.duree_s),
                          "fichiers": [{"langue": f.langue, "chemin": str(f.chemin), "nb": f.nb, "traduit": f.traduit} for f in r.fichiers]}
            if etapes.va_jusqua(jusqua, "doublage"):
                lot = self.lots.get(o.get("lot") or "")
                t.resultat.update(pipeline.doubler_et_assembler(d, t.prepare, r, jusqua, t.ecrire, etape, annule, bool((lot.options if lot else o).get("remplacer"))))
            t.etat = "terminee"
        except pipeline.Annulee:
            t.etat = "annulee"
            t.ecrire("Annulée.")
        except ERREURS_CONNUES as e:
            t.etat, t.erreur = "erreur", str(e)
            t.ecrire(f"Erreur : {e}")
        except Exception as e:  # noqa: BLE001 - une tâche ne doit pas tuer la boucle
            t.etat, t.erreur = "erreur", f"{type(e).__name__} : {e}"
            t.ecrire("Erreur inattendue :\n" + traceback.format_exc())
        finally:
            if t.etat in ("en_pause", "terminee"):
                self._fin_de_passe(t)
            if t.etat in ETATS_FINIS:
                t.fin = time.time()
                self._ecrire_journal(t)
                self._archiver(t)
            if t.options.get("lot"):
                self._maj_lot(t)

    def _relire(self, t: Tache, d: pipeline.Demande, cfg: dict, etape) -> list[str]:
        """Relecture IA si le lot la demande et qu'elle n'est pas déjà faite ; renvoie les noms de cette vidéo qui attendent une décision.
        Une panne d'API n'arrête pas la vidéo : seuls les noms validés sont appliqués, et elle se refera au prochain passage."""
        o = t.options
        if not o.get("fichier_lot"):
            return []
        fichier = t.prepare.travail / relecture.FICHIER_VIDEO
        if fichier.is_file() and not d.refaire:
            try:
                return sorted(set(json.loads(fichier.read_text(encoding="utf-8")).get("noms", [])) & relecture.noms_en_attente(o["fichier_lot"]))
            except (OSError, ValueError):
                pass
        if not o.get("relecture"):
            return []
        m = relecture.moteur(cfg, o.get("traducteur") or None)
        if m is None:
            t.ecrire("Relecture IA sautée : aucun traducteur Claude configuré.")
            return []
        etape("relecture")
        try:
            noms = relecture.relire(m, o["fichier_lot"], t.prepare.travail, t.prepare.video.name, [s.texte for s in t.prepare.sous_titres], t.ecrire, lambda: t.annulation)
        except (traduction.ErreurTraduction, ValueError, KeyError) as e:
            t.ecrire(f"Relecture IA non faite : {e}")
            noms = []
        if t.annulation:
            raise pipeline.Annulee()
        return noms

    def _fin_de_passe(self, t: Tache):
        """Retient la vitesse observée, pour le temps restant du lot et des prochaines sessions."""
        if t.options.get("continuer"):         # plusieurs étapes d'un coup : ne dit rien de la vitesse d'une passe
            return
        secondes, poids = time.time() - t.passe_debut, t.poids()
        if secondes < temps.DUREE_MIN_S or poids <= 0:
            return
        temps.noter(temps.cle(t.passe, t.options), secondes / poids)
        lot = self.lots.get(t.options.get("lot") or "")
        if lot:
            lot.noter_passe(poids, secondes)

    def _ecrire_journal(self, t: Tache):
        if not t.travail:
            return
        try:
            f = t.travail / f"journal-{time.strftime('%Y%m%d-%H%M', time.localtime(t.debut or t.creee))}-{t.id}.txt"
            entete = [f"# Tâche {t.id} : {t.options.get('source')}", f"# Options : {json.dumps(t.options, ensure_ascii=False)}",
                      f"# État final : {t.etat}" + (f" ({t.erreur})" if t.erreur else "")]
            f.write_text("\n".join(entete + t.journal) + "\n", encoding="utf-8")
            t.journal_fichier = str(f)
        except OSError as e:
            t.ecrire(f"Journal non enregistré : {e}")

    # --- Historique ---
    def _charger_historique(self) -> list[dict]:
        try:
            return json.loads(FICHIER_HISTORIQUE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _archiver(self, t: Tache):
        with self.verrou:
            self.historique = [h for h in self.historique if h["id"] != t.id]
            self.historique.insert(0, t.resume())
            del self.historique[50:]
            try:
                FICHIER_HISTORIQUE.parent.mkdir(parents=True, exist_ok=True)
                FICHIER_HISTORIQUE.write_text(json.dumps(self.historique, ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError:
                pass
