import json
import threading
from pathlib import Path

from soustitres import config, pipeline
from soustitres.web import taches
from soustitres.web.lots import Lot


def gestionnaire(tmp_path, monkeypatch):
    """Gestionnaire sans son fil d'exécution ni l'historique réel : rien ne se lance pour de bon."""
    monkeypatch.setattr(taches, "FICHIER_DERNIER_LOT", tmp_path / "dernier_lot.json")
    g = taches.Gestionnaire.__new__(taches.Gestionnaire)
    g.taches, g.ordre, g.lots, g.historique = {}, [], {}, []
    g.verrou, g.reveil = threading.Lock(), threading.Event()
    return g


def un_lot(g, tmp_path, etat="suspendu"):
    videos = [{"chemin": "D:/a.mp4", "nom": "a.mp4", "langue": "en", "etat": "fait", "tache": "t1", "nb": 1, "langue_detectee": "en", "erreur": ""},
              {"chemin": "D:/b.mp4", "nom": "b.mp4", "langue": "", "etat": "a_faire", "tache": None, "nb": None, "langue_detectee": "", "erreur": ""}]
    lot = Lot("D:/", videos, {"traducteur": "local", "langues": ["fr"], "pause": False}, tmp_path / "sous-titres.lot.json")
    lot.etat = etat
    lot.apres_enregistrement = g._noter_dernier_lot
    g.lots[lot.id] = lot
    lot.enregistrer()
    return lot


def test_reprendre_applique_le_nouveau_traducteur_aux_videos_suivantes(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    lot = un_lot(g, tmp_path)
    assert g.lot_reprendre(lot.id, {"traducteur": "claude", "langues": ["fr", "de"], "pause": True})
    g._alimenter_lots()
    t = g.taches[lot.videos[1]["tache"]]
    assert t.options["traducteur"] == "claude" and t.options["langues"] == ["fr", "de"] and t.options["pause"] is True


def test_reprendre_sans_modification_garde_les_reglages(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    lot = un_lot(g, tmp_path)
    assert g.lot_reprendre(lot.id, {})
    assert lot.options == {"traducteur": "local", "langues": ["fr"], "pause": False}


def test_dernier_lot_propose_a_la_session_suivante(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    lot = un_lot(g, tmp_path)
    assert g.dernier_lot() is None                    # déjà ouvert dans cette session
    suivant = gestionnaire(tmp_path, monkeypatch)
    d = suivant.dernier_lot()
    assert d["etat"] == "suspendu" and d["total"] == 2 and d["faites"] == 1 and d["traducteur"] == "local"
    assert Path(d["fichier"]) == lot.fichier
    suivant.oublier_dernier_lot()
    assert suivant.dernier_lot() is None and lot.fichier.exists()


def test_lot_termine_ou_fichier_disparu_non_propose(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    lot = un_lot(g, tmp_path)
    lot.etat = "termine"
    lot.enregistrer()
    assert gestionnaire(tmp_path, monkeypatch).dernier_lot() is None

    lot2 = un_lot(g, tmp_path)
    lot2.fichier.unlink()
    assert gestionnaire(tmp_path, monkeypatch).dernier_lot() is None


def prepare(travail, nb=10):
    travail.mkdir(parents=True, exist_ok=True)
    return pipeline.Prepare(travail / "v.mp4", travail, "v", "en", [object()] * nb, travail / "v.en.srt", travail, 0.0)


def test_langues_restantes(tmp_path):
    p = prepare(tmp_path)
    assert pipeline.langues_restantes(p, ["en", "fr", "de"]) == ["fr", "de"]
    (tmp_path / "traduction.de.json").write_text(json.dumps(["x"] * 10), encoding="utf-8")
    (tmp_path / "traduction.json").write_text(json.dumps(["x"] * 3), encoding="utf-8")   # autre découpage : à refaire
    assert pipeline.langues_restantes(p, ["fr", "de"]) == ["fr"]
    assert pipeline.langues_restantes(p, ["fr", "de"], refaire=True) == ["fr", "de"]


def test_estimation_cumulee_du_lot_sans_les_traductions_deja_faites(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "charger", lambda: {"traduction": {"lot": 25}, "traducteurs": [{"id": "claude", "type": "claude", "modele": "claude-sonnet-5-5"}]})
    lot = un_lot(g, tmp_path, etat="attente_traduction")
    for i, v in enumerate(lot.videos):
        t = taches.Tache({"source": v["chemin"], "lot": lot.id})
        t.prepare = prepare(tmp_path / f"v{i}")
        g.taches[t.id] = t
        v.update(etat="prepare", tache=t.id)
    e = g.estimer_lot(lot.id, ["fr"])
    assert e["videos"] == 2 and e["nb"] == 20 and e["requetes"] == 2 and e["jetons_entree"] == 20 * 36
    (tmp_path / "v0" / "traduction.fr.json").write_text(json.dumps(["x"] * 10), encoding="utf-8")
    e = g.estimer_lot(lot.id, ["fr"])
    assert e["videos"] == 1 and e["nb"] == 10
    (tmp_path / "v1" / "traduction.fr.json").write_text(json.dumps(["x"] * 10), encoding="utf-8")
    assert g.estimer_lot(lot.id, ["fr"])["langues"] == []


def test_derniere_video_traduite_seule_termine_le_lot(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    lot = un_lot(g, tmp_path, etat="attente_traduction")
    t = taches.Tache({"source": "D:/b.mp4", "lot": lot.id})
    t.etat = "terminee"
    lot.videos[1].update(etat="prepare", tache=t.id)
    g._maj_lot(t)
    assert lot.etat == "termine" and lot.videos[1]["etat"] == "fait"
    assert gestionnaire(tmp_path, monkeypatch).dernier_lot() is None


def test_lot_entierement_fait_non_propose_meme_si_son_etat_est_reste_ouvert(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    lot = un_lot(g, tmp_path, etat="attente_traduction")
    lot.videos[1]["etat"] = "fait"
    lot.enregistrer()
    assert gestionnaire(tmp_path, monkeypatch).dernier_lot() is None


def test_charger_un_lot_deja_ouvert_ne_bloque_pas(tmp_path, monkeypatch):
    g = gestionnaire(tmp_path, monkeypatch)
    lot = un_lot(g, tmp_path, etat="suspendu")
    resultat = []
    fil = threading.Thread(target=lambda: resultat.append(g.charger_lot(str(lot.fichier))), daemon=True)
    fil.start()
    fil.join(5)
    assert resultat and resultat[0].etat == "suspendu"
    assert gestionnaire(tmp_path, monkeypatch).dernier_lot() is not None     # le lot rechargé reste proposé
