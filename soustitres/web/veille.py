"""Coupure automatique de l'interface après une période sans activité (`[web] inactivite_min`, 0 = jamais).

Compte comme activité : une action de l'utilisateur (requête POST ou PUT, chargement de la page ou d'une donnée demandée
à la main) et tout le temps où une tâche ou un lot travaille. Ne compte pas : l'interrogation régulière faite par un
onglet resté ouvert ni le contrôle de santé de la page Nouvel onglet, sinon le service ne s'arrêterait jamais.
"""
import re
import time

_INTERROGATION = re.compile(r"^/api/(sante|etat|taches|lots)(/|$)")


def est_activite(methode: str, chemin: str) -> bool:
    return methode != "GET" or _INTERROGATION.match(chemin.split("?", 1)[0]) is None


class Veille:
    def __init__(self, delai_s, occupe, horloge=time.monotonic):
        self.delai_s = delai_s                 # fonction : relue à chaque contrôle (un changement dans Réglages s'applique aussitôt)
        self.occupe = occupe                   # fonction : True tant qu'une tâche ou un lot travaille
        self.horloge = horloge
        self.derniere = horloge()

    def signaler(self):
        self.derniere = self.horloge()

    def expiree(self) -> bool:
        if self.occupe():
            self.signaler()                    # le délai court à partir de la fin du travail
            return False
        delai = self.delai_s()
        return delai > 0 and self.horloge() - self.derniere >= delai

    def restant_s(self) -> float | None:
        """Secondes avant l'arrêt ; None si aucun décompte ne court (travail en cours, ou arrêt désactivé)."""
        delai = self.delai_s()
        if delai <= 0 or self.occupe():
            return None
        return max(0.0, delai - (self.horloge() - self.derniere))
