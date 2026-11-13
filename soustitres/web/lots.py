"""Lot de vidéos (source = un dossier) : la file enchaîne une tâche par vidéo, phase 1 pour toutes (sous-titres dans
la langue parlée), puis, après « Traduire tout », la phase 2 pour chacune. Le fichier de lot (`sous-titres.lot.json`,
dans le dossier de sortie si la destination « dossier de sortie » est cochée, sinon dans le dossier source) est
réécrit à chaque changement : sélection, langue par vidéo, avancement. Après une coupure, « Reprendre un lot »
le relit et ne refait que ce qui manque (les vidéos « faites » sont sautées ; les autres repartent de la phase 1,
rapide grâce à transcription.json conservé)."""
import threading
import time
import uuid
from pathlib import Path

from .. import dossier, etapes
from . import temps

ETATS_VIDEO = ("a_faire", "en_cours", "prepare", "traduit", "fait", "erreur", "annule")
VOULU = {1: "a_faire", 2: "prepare", 3: "traduit"}   # état des vidéos que chaque phase prend en charge


class Lot:
    def __init__(self, racine: str, videos: list[dict], options: dict, fichier: Path, ident: str | None = None):
        self.id = ident or uuid.uuid4().hex[:8]
        self.racine = racine
        self.videos = videos                   # [{chemin, nom, langue, etat, tache, nb, langue_detectee, erreur}]
        self.options = options                 # transcription, traducteur, langues, destination, sortie, pause, arret, relecture
        self.fichier = Path(fichier)
        self.etat = "en_cours"                 # en_cours, suspendu, attente_traduction, termine, annule
        self.phase = 1
        self.creee = time.time()
        self.fin = None
        self.apres_enregistrement = None       # rappel du gestionnaire (mémorise le dernier lot pour la session suivante)
        self.etape_de = None                   # rappel du gestionnaire : étape en cours d'une tâche (id → « transcription »…)
        self.passes: dict[int, list[tuple[float, float]]] = {}   # phase → [(poids, secondes)] des vidéos traitées
        self.simultanees = 1                   # vidéos traitées en même temps (plusieurs : traduction par une API distante)
        self.verrou = threading.RLock()        # les tâches parallèles mettent à jour le lot et réécrivent son fichier

    # --- sérialisation ---
    def vers_dict(self) -> dict:
        return {"id": self.id, "racine": self.racine, "options": self.options, "etat": self.etat, "phase": self.phase,
                "creee": self.creee, "fin": self.fin, "fichier": str(self.fichier).replace("\\", "/"), "videos": self.videos}

    def resume(self) -> dict:
        comptes = {e: sum(1 for v in self.videos if v["etat"] == e) for e in ETATS_VIDEO}
        mesures = self.passes.get(self.phase, [])[-10:]
        courante = self.en_cours()
        # Sans pause, une vidéo de la phase 1 va jusqu'à la traduction : la phase seule ne dit pas où elle en est.
        etape = (self.etape_de(courante.get("tache")) or "") if courante and self.etape_de else ""
        return {**self.vers_dict(), "comptes": comptes, "etape": etape, "total": len(self.videos), "simultanees": self.simultanees,
                "duree_moyenne_s": round(sum(s for _, s in mesures) / len(mesures)) if mesures else None,
                "courante": courante["chemin"] if courante else None,
                "reste_s": self.reste_s(temps.taux_connu(self.phase, self.options) if self.etat == "en_cours" else None),
                "doublage_s": self.doublage_a_faire_s()}

    def enregistrer(self):
        with self.verrou:
            try:
                dossier.ecrire_lot(self.fichier, self.vers_dict())
            except OSError:
                pass
            if self.apres_enregistrement:
                self.apres_enregistrement(self)

    # --- avancement ---
    def prochaine(self) -> dict | None:
        voulu = VOULU[self.phase]
        # En phase 2, une vidéo remise à faire (« Réessayer ») repart de la transcription, sans pause.
        a_traiter = [v for v in self.videos if v["etat"] == voulu] or [v for v in self.videos if v["etat"] == "a_faire"]
        # Reprise : les transcriptions déjà faites passent d'abord (quelques secondes chacune), le compteur est vite juste.
        return next((v for v in a_traiter if v["etat"] == "a_faire" and v.get("transcrite")), a_traiter[0] if a_traiter else None)

    def en_cours(self) -> dict | None:
        return next((v for v in self.videos if v["etat"] == "en_cours"), None)

    def poids(self, v: dict) -> float:
        """Ce qui fait varier la durée d'une vidéo dans cette phase (voir temps.py) ; 0 si encore inconnu."""
        if self.phase in (1, 3):
            return float(v.get("duree") or 0)
        return float(v.get("nb") or 0) * max(1, len(self.options.get("langues") or []))

    def noter_passe(self, poids: float, secondes: float):
        self.passes.setdefault(self.phase, []).append((poids, secondes))

    def reste_s(self, taux_connu: float | None = None, maintenant: float | None = None) -> int | None:
        """Secondes avant la fin de la phase en cours, d'après les 10 dernières vidéos traitées, sinon `taux_connu`.
        None si le lot ne travaille pas ou si rien ne permet d'estimer."""
        if self.etat != "en_cours":
            return None
        voulu = VOULU[self.phase]
        a_transcrire = lambda v: self.phase != 1 or not v.get("transcrite")  # noqa: E731 - une reprise ne coûte rien
        a_venir = [v for v in self.videos if v["etat"] == voulu and a_transcrire(v)]
        courantes = [v for v in self.videos if v["etat"] == "en_cours" and a_transcrire(v)]
        mesures = self.passes.get(self.phase, [])[-10:]
        poids_mesure = sum(p for p, _ in mesures)
        taux = sum(s for _, s in mesures) / poids_mesure if poids_mesure > 0 else taux_connu
        connus = [p for p in map(self.poids, a_venir + courantes) if p > 0]
        if not taux or not connus:
            return None
        moyen = sum(connus) / len(connus)
        reste = sum(self.poids(v) or moyen for v in a_venir) * taux
        for v in courantes:
            ecoule = (time.time() if maintenant is None else maintenant) - v.get("debut", time.time())
            reste += max(0.0, (self.poids(v) or moyen) * taux - ecoule)
        return round(reste / max(1, self.simultanees))

    def avancement(self) -> str:
        """Texte court pour le module Service local : « lot 3/22 », vidéo en cours comprise."""
        voulu = VOULU[self.phase]
        total = len(self.videos)
        rang = max(1, total - sum(1 for v in self.videos if v["etat"] == voulu))
        return f"lot {rang}/{total}" + {1: "", 2: " (traduction)", 3: " (doublage)"}[self.phase]

    def doublage_a_faire_s(self) -> int:
        """Temps de carte graphique pour les vidéos encore à doubler ; 0 si ce lot ne veut que les sous-titres."""
        if not etapes.va_jusqua(self.options.get("arret"), "doublage"):
            return 0
        a_faire = [v for v in self.videos if not v.get("assemblee") and v["etat"] not in ("annule", "erreur")]
        return round(sum(float(v.get("duree") or 0) for v in a_faire) * (temps.taux_connu(3, self.options) or 0))

    def video_de(self, tache_id: str) -> dict | None:
        return next((v for v in self.videos if v.get("tache") == tache_id), None)

    def terminer_phase(self):
        """Plus rien à lancer dans la phase courante."""
        if self.phase == 1 and any(v["etat"] == "prepare" for v in self.videos):
            self.etat = "attente_traduction"
        elif self.phase < 3 and any(v["etat"] == "traduit" for v in self.videos):
            self.phase, self.etat = 3, "en_cours"
        else:
            self.etat, self.fin = "termine", time.time()

    @classmethod
    def depuis_fichier(cls, fichier: Path) -> "Lot":
        """Lot relu pour une reprise : les vidéos non terminées repartent de la phase 1 (rapide : transcription conservée)."""
        d = dossier.lire_lot(fichier)
        videos = []
        for v in d["videos"]:
            etat = v["etat"] if v.get("etat") in ("fait", "traduit") else "a_faire"     # traduite : seul le doublage reste à faire
            videos.append({**v, "etat": etat, "tache": None if etat != "fait" else v.get("tache"), "erreur": ""})
        lot = cls(d["racine"], videos, d.get("options", {}), Path(d["fichier"]), d.get("id"))
        lot.etat = "suspendu"
        lot.creee = d.get("creee") or time.time()
        return lot
