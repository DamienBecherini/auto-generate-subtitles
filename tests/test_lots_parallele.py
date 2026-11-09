"""Traduction d'un lot par une API distante : plusieurs vidéos à la fois, jamais plus que permis."""
import threading
from types import SimpleNamespace

from soustitres import config, traduction
from soustitres.web import taches
from soustitres.web.lots import Lot
from soustitres.web.taches import Gestionnaire, Tache

CLAUDE = {"id": "claude", "nom": "Claude", "type": "claude"}
LOCAL = {"id": "local", "nom": "Local", "type": "openai"}


def gestionnaire(monkeypatch, tmp_path, traducteur):
    monkeypatch.setattr(taches, "FICHIER_DERNIER_LOT", tmp_path / "dernier_lot.json")
    monkeypatch.setattr(config, "charger", lambda: {"traducteurs": [CLAUDE, LOCAL, {**CLAUDE, "id": "deux", "parallele": 2}],
                                                    "traducteur_actif": traducteur, "sortie": str(tmp_path)})
    g = Gestionnaire.__new__(Gestionnaire)
    g.taches, g.ordre, g.lots, g.historique = {}, [], {}, []
    g.verrou, g.reveil = threading.Lock(), threading.Event()
    return g


def lot_en_phase_2(g, tmp_path, nb, traducteur):
    videos = []
    for i in range(nb):
        t = Tache({"source": f"v{i}.mp4", "lot": "L"})
        t.etat, t.prepare = "en_pause", SimpleNamespace(sous_titres=[1] * 100, code="en")
        g.taches[t.id] = t
        g.ordre.append(t.id)
        videos.append({"chemin": f"v{i}.mp4", "nom": f"v{i}.mp4", "etat": "prepare", "tache": t.id, "nb": 100, "erreur": ""})
    lot = Lot("r", videos, {"traducteur": traducteur}, tmp_path / "lot.json", "L")
    lot.phase = 2
    g.lots["L"] = lot
    return lot


def test_nombre_de_videos_simultanees_selon_le_traducteur():
    assert traduction.simultanees(CLAUDE) == 5
    assert traduction.simultanees(LOCAL) == 1
    assert traduction.simultanees({**CLAUDE, "parallele": 3}) == 3
    assert traduction.simultanees({**LOCAL, "parallele": "abc"}) == 1
    assert traduction.simultanees({**CLAUDE, "parallele": 500}) == traduction.SIMULTANEES_MAX


def test_claude_lance_cinq_videos_et_pas_plus(monkeypatch, tmp_path):
    g = gestionnaire(monkeypatch, tmp_path, "claude")
    lot = lot_en_phase_2(g, tmp_path, 8, "claude")
    g._alimenter_lots()
    g._alimenter_lots()                         # un second passage ne lance rien de plus
    assert sum(v["etat"] == "en_cours" for v in lot.videos) == 5
    assert sum(g.taches[v["tache"]].etat == "en_attente" for v in lot.videos) == 5


def test_le_reglage_parallele_du_traducteur_est_respecte(monkeypatch, tmp_path):
    g = gestionnaire(monkeypatch, tmp_path, "deux")
    lot = lot_en_phase_2(g, tmp_path, 8, "deux")
    g._alimenter_lots()
    assert sum(v["etat"] == "en_cours" for v in lot.videos) == 2


def test_ia_locale_reste_a_une_video(monkeypatch, tmp_path):
    g = gestionnaire(monkeypatch, tmp_path, "local")
    lot = lot_en_phase_2(g, tmp_path, 8, "local")
    g._alimenter_lots()
    assert sum(v["etat"] == "en_cours" for v in lot.videos) == 1


def test_la_transcription_reste_une_a_la_fois(monkeypatch, tmp_path):
    g = gestionnaire(monkeypatch, tmp_path, "claude")
    lot = lot_en_phase_2(g, tmp_path, 8, "claude")
    lot.phase = 1
    for v in lot.videos:
        v["etat"], v["tache"] = "a_faire", None
    g._alimenter_lots()
    assert sum(v["etat"] == "en_cours" for v in lot.videos) == 1


def test_une_place_liberee_lance_la_suivante(monkeypatch, tmp_path):
    g = gestionnaire(monkeypatch, tmp_path, "claude")
    lot = lot_en_phase_2(g, tmp_path, 8, "claude")
    g._alimenter_lots()
    premiere = lot.en_cours()
    t = g.taches[premiere["tache"]]
    t.etat = "terminee"
    g._maj_lot(t)
    assert premiere["etat"] == "fait"
    g._alimenter_lots()
    assert sum(v["etat"] == "en_cours" for v in lot.videos) == 5
    assert sum(v["etat"] == "prepare" for v in lot.videos) == 2


def test_la_phase_ne_finit_pas_tant_qu_une_video_tourne(monkeypatch, tmp_path):
    g = gestionnaire(monkeypatch, tmp_path, "claude")
    lot = lot_en_phase_2(g, tmp_path, 2, "claude")
    g._alimenter_lots()
    a, b = lot.videos
    t = g.taches[a["tache"]]
    t.etat = "terminee"
    g._maj_lot(t)
    g._alimenter_lots()
    assert lot.etat == "en_cours"
    t = g.taches[b["tache"]]
    t.etat = "terminee"
    g._maj_lot(t)
    g._alimenter_lots()
    assert lot.etat == "termine"


def test_pause_du_lot_ne_lance_plus_rien(monkeypatch, tmp_path):
    g = gestionnaire(monkeypatch, tmp_path, "claude")
    lot = lot_en_phase_2(g, tmp_path, 8, "claude")
    g._alimenter_lots()
    g.lot_suspendre("L")
    t = g.taches[lot.en_cours()["tache"]]
    t.etat = "terminee"
    g._maj_lot(t)
    g._alimenter_lots()
    assert sum(v["etat"] == "en_cours" for v in lot.videos) == 4


def test_les_traductions_paralleles_tournent_dans_leur_propre_fil(monkeypatch, tmp_path):
    g = gestionnaire(monkeypatch, tmp_path, "claude")
    lot = lot_en_phase_2(g, tmp_path, 3, "claude")
    g._alimenter_lots()
    ensemble = threading.Barrier(3, timeout=5)     # échoue si les trois ne tournent pas en même temps
    fini = []

    def executer(t):
        ensemble.wait()
        fini.append(t.id)

    monkeypatch.setattr(g, "_executer", executer)
    fils_avant = set(threading.enumerate())
    for v in lot.videos:
        g._demarrer(g.taches[v["tache"]])
    for f in set(threading.enumerate()) - fils_avant:
        f.join(6)
    assert len(fini) == 3


def test_temps_restant_divise_par_le_nombre_de_videos_simultanees(tmp_path):
    videos = [{"chemin": f"v{i}", "nom": f"v{i}", "etat": "prepare", "tache": None, "nb": 100, "erreur": ""} for i in range(10)]
    lot = Lot("r", videos, {"langues": ["fr"]}, tmp_path / "lot.json", "L")
    lot.phase = 2
    seul = lot.reste_s(taux_connu=0.2)
    lot.simultanees = 5
    assert seul == 200
    assert lot.reste_s(taux_connu=0.2) == 40
