"""Reprise d'un lot : les vidéos déjà transcrites passent d'abord, et ne comptent pas dans le temps restant."""
import threading

from soustitres import config, pipeline
from soustitres.web import taches
from soustitres.web.lots import Lot


def video(nom, etat="a_faire", **autres):
    return {"chemin": f"D:/{nom}.mp4", "nom": f"{nom}.mp4", "langue": "", "etat": etat, "tache": None, "nb": None,
            "langue_detectee": "", "erreur": "", "duree": 100, **autres}


def lot_de(tmp_path, videos, options=None, phase=1):
    lot = Lot("D:/", videos, options or {}, tmp_path / "sous-titres.lot.json")
    lot.phase = phase
    return lot


def test_les_videos_deja_transcrites_passent_d_abord(tmp_path):
    lot = lot_de(tmp_path, [video("a"), video("b", transcrite=True), video("c", transcrite=True)])
    assert lot.prochaine() is lot.videos[1]
    lot.videos[1]["etat"] = "prepare"
    assert lot.prochaine() is lot.videos[2]
    lot.videos[2]["etat"] = "prepare"
    assert lot.prochaine() is lot.videos[0]


def test_sans_transcription_conservee_l_ordre_du_dossier_est_garde(tmp_path):
    lot = lot_de(tmp_path, [video("a"), video("b")])
    assert lot.prochaine() is lot.videos[0]


def test_en_phase_2_les_videos_pretes_restent_dans_l_ordre(tmp_path):
    lot = lot_de(tmp_path, [video("a", "prepare"), video("b", "prepare", transcrite=True)], phase=2)
    assert lot.prochaine() is lot.videos[0]


def test_lot_fini_plus_rien_a_lancer(tmp_path):
    assert lot_de(tmp_path, [video("a", "prepare")]).prochaine() is None


def test_temps_restant_ignore_les_transcriptions_conservees(tmp_path):
    lot = lot_de(tmp_path, [video("a"), video("b", transcrite=True), video("c", transcrite=True)])
    assert lot.reste_s(taux_connu=2.0) == 200


def test_reperage_d_apres_le_dossier_de_travail(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "charger", lambda: {"sortie": str(tmp_path)})
    videos = [video("a"), video("b")]
    travail = pipeline.dossier_travail(tmp_path, videos[1]["chemin"])
    travail.mkdir(parents=True)
    (travail / "transcription.json").write_text("{}", encoding="utf-8")
    lot = lot_de(tmp_path, videos)
    taches.Gestionnaire._reperer_transcrites(lot)
    assert [v["transcrite"] for v in lot.videos] == [False, True]


def test_refaire_retranscrit_tout(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "charger", lambda: {"sortie": str(tmp_path)})
    videos = [video("a")]
    travail = pipeline.dossier_travail(tmp_path, videos[0]["chemin"])
    travail.mkdir(parents=True)
    (travail / "transcription.json").write_text("{}", encoding="utf-8")
    lot = lot_de(tmp_path, videos, {"refaire": True})
    taches.Gestionnaire._reperer_transcrites(lot)
    assert lot.videos[0]["transcrite"] is False


def test_reprise_repere_les_transcriptions(tmp_path, monkeypatch):
    monkeypatch.setattr(taches, "FICHIER_DERNIER_LOT", tmp_path / "dernier_lot.json")
    monkeypatch.setattr(config, "charger", lambda: {"sortie": str(tmp_path)})
    g = taches.Gestionnaire.__new__(taches.Gestionnaire)
    g.taches, g.ordre, g.lots, g.historique = {}, [], {}, []
    g.verrou, g.reveil = threading.Lock(), threading.Event()
    monkeypatch.setattr(g, "_mesurer_durees", lambda lot: None)
    videos = [video("a"), video("b")]
    travail = pipeline.dossier_travail(tmp_path, videos[1]["chemin"])
    travail.mkdir(parents=True)
    (travail / "transcription.json").write_text("{}", encoding="utf-8")
    fichier = tmp_path / "sous-titres.lot.json"
    lot_de(tmp_path, videos).enregistrer()
    repris = g.charger_lot(str(fichier))
    assert repris.prochaine() is repris.videos[1]
