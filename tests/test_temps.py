import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from soustitres import config
from soustitres.web import taches, temps
from soustitres.web.lots import Lot
from soustitres.web.taches import Gestionnaire, Tache


@pytest.fixture(autouse=True)
def memoire_isolee(tmp_path, monkeypatch):
    monkeypatch.setattr(temps, "FICHIER", tmp_path / "vitesses.json")
    monkeypatch.setattr(temps, "_memoire", None)


def lot_de(etats, duree=600, phase=1, options=None):
    videos = [{"chemin": f"/v{i}.mp4", "etat": e, "duree": duree, "nb": 100} for i, e in enumerate(etats)]
    lot = Lot("/r", videos, options or {}, Path("/r/f.json"))
    lot.phase = phase
    return lot


def test_rien_a_estimer_sans_mesure_ni_vitesse():
    assert lot_de(["en_cours", "a_faire"]).reste_s() is None


def test_lot_a_l_arret_n_a_pas_de_temps_restant():
    lot = lot_de(["a_faire"])
    lot.etat = "suspendu"
    assert lot.reste_s(taux_connu=0.1) is None


def test_vitesse_connue_avant_la_premiere_mesure():
    lot = lot_de(["a_faire", "a_faire"])
    assert lot.reste_s(taux_connu=0.1) == 120


def test_la_vitesse_observee_passe_avant_la_vitesse_connue():
    lot = lot_de(["fait", "a_faire"])
    lot.noter_passe(600, 300)
    assert lot.reste_s(taux_connu=0.1) == 300


def test_le_temps_deja_passe_sur_la_video_courante_est_deduit():
    lot = lot_de(["en_cours", "a_faire"])
    lot.videos[0]["debut"] = 1000
    assert lot.reste_s(taux_connu=0.1, maintenant=1040) == 20 + 60


def test_une_video_qui_depasse_ne_rend_pas_le_temps_negatif():
    lot = lot_de(["en_cours"])
    lot.videos[0]["debut"] = 0
    assert lot.reste_s(taux_connu=0.1, maintenant=1e6) == 0


def test_une_video_plus_longue_compte_davantage():
    lot = lot_de(["a_faire", "a_faire"])
    lot.videos[0]["duree"], lot.videos[1]["duree"] = 600, 1800
    assert lot.reste_s(taux_connu=0.1) == 240


def test_duree_inconnue_remplacee_par_la_moyenne():
    lot = lot_de(["a_faire", "a_faire"])
    lot.videos[1]["duree"] = 0
    assert lot.reste_s(taux_connu=0.1) == 120


def test_traduction_comptee_en_lignes_et_langues():
    lot = lot_de(["prepare"], phase=2, options={"langues": ["fr", "de"]})
    assert lot.reste_s(taux_connu=0.5) == 100


def test_cles_distinctes_selon_ce_qui_travaille():
    o = {"transcription": "t", "traducteur": "c", "pause": True}
    assert temps.cle(1, o) == "transcription|t"
    assert temps.cle(1, {**o, "pause": False}) == "complet|t|c"
    assert temps.cle(2, o) == "traduction|c"


def test_vitesse_retenue_d_une_session_a_l_autre(monkeypatch):
    temps.noter("x", 0.2)
    temps.noter("x", 0.4)
    monkeypatch.setattr(temps, "_memoire", None)
    assert temps.lire("x") == pytest.approx(0.3)


def cfg(pause_type="openai"):
    return {"traducteur_actif": "t", "transcription_active": "s", "estimation": {"secondes_par_ligne_local": 0.5},
            "traducteurs": [{"id": "t", "type": pause_type}], "transcriptions": [{"id": "s", "vitesse": 10}]}


def test_vitesse_annoncee_du_serveur(monkeypatch):
    monkeypatch.setattr(config, "charger", cfg)
    assert temps.taux_connu(1, {"pause": True}) == pytest.approx(0.1)
    assert temps.taux_connu(2, {}) == pytest.approx(0.5)


def test_sans_pause_la_traduction_est_comptee(monkeypatch):
    monkeypatch.setattr(config, "charger", cfg)
    assert temps.taux_connu(1, {"pause": False, "langues": ["fr"]}) == pytest.approx(0.1 + temps.LIGNES_PAR_SECONDE * 0.5)


def test_traducteur_claude_sans_duree_annoncee(monkeypatch):
    monkeypatch.setattr(config, "charger", lambda: cfg("claude"))
    assert temps.taux_connu(2, {}) is None


def tache_en_cours(duree=600, **options):
    t = Tache({"source": "/v.mp4", "duree_video": duree, **options})
    t.etat, t.passe_debut = "en_cours", 1000.0
    return t


def test_tache_seule(monkeypatch):
    monkeypatch.setattr(temps, "taux_connu", lambda *_: 0.1)
    assert tache_en_cours().reste_s(maintenant=1020) == 40


def test_tache_sans_duree_ni_etat_actif(monkeypatch):
    monkeypatch.setattr(temps, "taux_connu", lambda *_: 0.1)
    assert tache_en_cours(duree=0).reste_s() is None
    t = tache_en_cours()
    t.etat = "en_pause"
    assert t.reste_s() is None


def test_tache_en_traduction_comptee_en_lignes(monkeypatch):
    monkeypatch.setattr(temps, "taux_connu", lambda *_: 0.5)
    t = tache_en_cours(langues=["fr", "de"])
    t.passe, t.prepare = 2, SimpleNamespace(sous_titres=[0] * 100)
    assert t.reste_s(maintenant=1000) == 100


def gestionnaire(tmp_path, monkeypatch):
    monkeypatch.setattr(taches, "FICHIER_DERNIER_LOT", tmp_path / "dernier_lot.json")
    g = Gestionnaire.__new__(Gestionnaire)
    g.taches, g.ordre, g.lots, g.historique = {}, [], {}, []
    g.verrou, g.reveil = threading.Lock(), threading.Event()
    return g


def test_fin_de_passe_retient_la_vitesse(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    lot = g.lots["L"] = lot_de(["en_cours"])
    t = tache_en_cours(lot="L", transcription="s", pause=True)
    t.passe_debut = time.time() - 60
    g._fin_de_passe(t)
    assert temps.lire("transcription|s") == pytest.approx(0.1, abs=0.01)
    assert len(lot.passes[1]) == 1


def test_passe_reprise_en_quelques_secondes_ignoree(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    t = tache_en_cours(transcription="s", pause=True)
    t.passe_debut = time.time() - 1
    g._fin_de_passe(t)
    assert temps.lire("transcription|s") is None
