from pathlib import Path

from soustitres.web import serveur
from soustitres.web.lots import Lot
from soustitres.web.veille import Veille, est_activite


class Horloge:
    t = 0.0

    def __call__(self):
        return self.t


def test_interrogations_et_actions():
    for chemin in ("/api/sante", "/api/etat", "/api/taches", "/api/taches/ab12?depuis=3", "/api/lots/ab12", "/api/lots/ab12/estimation"):
        assert not est_activite("GET", chemin), chemin
    assert est_activite("GET", "/") and est_activite("GET", "/api/config") and est_activite("GET", "/api/nettoyage")
    assert est_activite("POST", "/api/taches") and est_activite("PUT", "/api/config")


def test_expire_apres_le_delai_et_une_action_le_repousse():
    h = Horloge()
    v = Veille(lambda: 3600, lambda: False, h)
    h.t = 3599
    assert not v.expiree()
    v.signaler()
    h.t = 3599 + 3599
    assert not v.expiree()
    h.t = 3599 + 3600
    assert v.expiree()


def test_le_delai_court_depuis_la_fin_du_travail():
    h = Horloge()
    occupe = [True]
    v = Veille(lambda: 3600, lambda: occupe[0], h)
    h.t = 10000
    assert not v.expiree()
    occupe[0] = False
    h.t = 10000 + 3599
    assert not v.expiree()
    h.t = 10000 + 3600
    assert v.expiree()


def test_zero_desactive():
    h = Horloge()
    v = Veille(lambda: 0, lambda: False, h)
    h.t = 1e9
    assert not v.expiree()
    assert v.restant_s() is None


def test_temps_restant():
    h = Horloge()
    occupe = [False]
    v = Veille(lambda: 3600, lambda: occupe[0], h)
    h.t = 600
    assert v.restant_s() == 3000
    occupe[0] = True
    assert v.restant_s() is None
    h.t = 1e6
    occupe[0] = False
    assert v.restant_s() == 0


def test_avancement_d_un_lot():
    videos = [{"etat": e} for e in ("fait", "prepare", "en_cours", "a_faire", "a_faire")]
    lot = Lot("/r", videos, {}, Path("/r/f.json"))
    assert lot.avancement() == "lot 3/5"
    lot.phase = 2
    assert lot.avancement() == "lot 4/5 (traduction)"


def test_reponse_de_sante_selon_le_contrat(monkeypatch):
    class Gestionnaire:
        def occupe(self):
            return True

        def activite(self):
            return "lot 3/22"

        def reste_s(self):
            return 720

    monkeypatch.setattr(serveur, "gestionnaire", Gestionnaire())
    monkeypatch.setattr(serveur, "delai_inactivite_s", lambda: 3600)
    monkeypatch.setattr(serveur, "veille", Veille(lambda: 3600, lambda: True))
    d = serveur.etat_sante()
    assert (d["etat"], d["contrat"], d["service"], d["occupe"], d["activite"]) == ("pret", 1, "sous-titres", True, "lot 3/22")
    assert d["reste_travail_s"] == 720
    assert d["inactivite"] == {"delai_s": 3600, "restant_s": None}
