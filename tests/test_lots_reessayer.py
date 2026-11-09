"""« Réessayer » une vidéo en erreur d'un lot, quel que soit l'état du lot."""
import threading

from soustitres.web import taches
from soustitres.web.lots import Lot


def gestionnaire(tmp_path, monkeypatch):
    monkeypatch.setattr(taches, "FICHIER_DERNIER_LOT", tmp_path / "dernier_lot.json")
    g = taches.Gestionnaire.__new__(taches.Gestionnaire)
    g.taches, g.ordre, g.lots, g.historique = {}, [], {}, []
    g.verrou, g.reveil = threading.Lock(), threading.Event()
    return g


def un_lot(g, tmp_path, etats, etat="en_cours", phase=1, options=None):
    videos = [{"chemin": f"/v{i}.mp4", "nom": f"v{i}.mp4", "langue": "", "etat": e, "tache": f"t{i}", "nb": 10, "langue_detectee": "",
               "erreur": "Pas de réponse" if e == "erreur" else ""} for i, e in enumerate(etats)]
    lot = Lot("/r", videos, options or {"pause": True}, tmp_path / "sous-titres.lot.json")
    lot.etat, lot.phase = etat, phase
    g.lots[lot.id] = lot
    return lot


def test_la_video_repart_de_zero(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    lot = un_lot(g, tmp_path, ["prepare", "erreur", "a_faire"])
    assert g.lot_reessayer(lot.id, "/v1.mp4")
    v = lot.videos[1]
    assert (v["etat"], v["tache"], v["erreur"]) == ("a_faire", None, "")
    assert g.reveil.is_set()


def test_une_seule_video_visee(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    lot = un_lot(g, tmp_path, ["erreur", "erreur"])
    g.lot_reessayer(lot.id, "/v0.mp4")
    assert [v["etat"] for v in lot.videos] == ["a_faire", "erreur"]


def test_toutes_les_videos_en_erreur(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    lot = un_lot(g, tmp_path, ["erreur", "prepare", "erreur"])
    g.lot_reessayer(lot.id)
    assert [v["etat"] for v in lot.videos] == ["a_faire", "prepare", "a_faire"]


def test_lot_en_pause_reste_en_pause(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    lot = un_lot(g, tmp_path, ["erreur"], etat="suspendu")
    assert g.lot_reessayer(lot.id)
    assert lot.etat == "suspendu"


def test_lot_termine_repart(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    lot = un_lot(g, tmp_path, ["fait", "erreur"], etat="termine", phase=2)
    lot.fin = 123.0
    assert g.lot_reessayer(lot.id)
    assert (lot.etat, lot.fin) == ("en_cours", None)


def test_lot_en_attente_de_traduction_finit_d_abord_cette_video(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    lot = un_lot(g, tmp_path, ["prepare", "erreur"], etat="attente_traduction")
    assert g.lot_reessayer(lot.id)
    assert (lot.etat, lot.phase) == ("en_cours", 1)
    assert lot.prochaine() is lot.videos[1]


def test_refus_sans_video_en_erreur_ou_lot_annule(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    assert not g.lot_reessayer(un_lot(g, tmp_path, ["prepare"]).id)
    assert not g.lot_reessayer(un_lot(g, tmp_path, ["erreur"], etat="annule").id)
    assert not g.lot_reessayer("inconnu")


def test_phase_1_relance_avec_les_options_du_lot(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    lot = un_lot(g, tmp_path, ["erreur"])
    g.lot_reessayer(lot.id)
    g._alimenter_lots()
    t = next(iter(g.taches.values()))
    assert t.options["source"] == "/v0.mp4" and t.options["pause"] is True
    assert (lot.videos[0]["etat"], lot.videos[0]["tache"]) == ("en_cours", t.id)


def test_phase_2_refait_toute_la_video_sans_pause(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    lot = un_lot(g, tmp_path, ["fait", "erreur"], phase=2)
    g.lot_reessayer(lot.id)
    g._alimenter_lots()
    t = next(iter(g.taches.values()))
    assert t.options["source"] == "/v1.mp4" and t.options["pause"] is False


def test_phase_2_traduit_d_abord_les_videos_pretes(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    lot = un_lot(g, tmp_path, ["a_faire", "prepare"], phase=2)
    assert lot.prochaine() is lot.videos[1]
