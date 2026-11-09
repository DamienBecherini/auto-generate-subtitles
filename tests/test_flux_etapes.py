"""Flux par étapes : relecture, validation, continuation d'un épisode, phases du lot, assemblage et lancement du doublage."""
import json
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from soustitres import assemblage, config, dossier, doublage, etapes, glossaire, pipeline, relecture
from soustitres.web import taches
from soustitres.web.lots import Lot
from soustitres.web.taches import Gestionnaire, Tache

CLAUDE = {"id": "claude", "nom": "Claude", "type": "claude"}


class FauxTraducteur:
    def __init__(self, reponses):
        self.reponses, self.demandes, self.termine = list(reponses), [], False

    def completer(self, consigne, demande, schema=None, effort=None):
        self.demandes.append(demande)
        return json.dumps({"entrees": self.reponses.pop(0)})

    def terminer(self):
        self.termine = True


def entree(i, statut="a_corriger", graphie="Gariaev", francais="Gariaev", confiance="haute"):
    return {"index": i, "statut": statut, "graphie": graphie, "francais": francais, "confiance": confiance, "raison": "son proche"}


def test_candidats_ecarte_les_mots_courants_et_le_possessif():
    t = "We read Gary Iev's work. Science is good. The science of science. the Pyotr Gary Iev spoke."
    noms = {c["nom"] for c in relecture.candidats(t)}
    assert "Gary Iev" in noms and "Pyotr Gary Iev" in noms
    assert "Science" not in noms


def test_un_nom_nest_soumis_quune_fois_par_lot(tmp_path):
    lot = tmp_path / "lot.json"
    t1, t2 = tmp_path / "a", tmp_path / "b"
    t1.mkdir(), t2.mkdir()
    faux = FauxTraducteur([[entree(0)], []])
    creer = lambda m, j: faux  # noqa: E731
    assert relecture.relire(CLAUDE, lot, t1, "Episode", ["We saw Gary Iev come."], journal=lambda _m: None, creer=creer) == ["Gary Iev"]
    assert relecture.relire(CLAUDE, lot, t2, "Episode", ["We saw Gary Iev and Walter Russell speak."], journal=lambda _m: None, creer=creer) == ["Gary Iev"]
    assert "0. Walter Russell" in faux.demandes[1] and "Gary Iev (" not in faux.demandes[1]
    assert faux.termine


def test_validation_alimente_le_glossaire_et_libere_la_video(tmp_path):
    lot = tmp_path / "lot.json"
    t = tmp_path / "a"
    t.mkdir()
    faux = FauxTraducteur([[entree(0), entree(1, statut="incertain", graphie="", francais="")]])
    relecture.relire(CLAUDE, lot, t, "E", ["We saw Gary Iev and Zork Blim come."], journal=lambda _m: None, creer=lambda m, j: faux)
    attente = relecture.noms_en_attente(lot)
    video = tmp_path / "v.mp4"
    assert etapes.constater(t, video, ["fr"], "en", attente)["validee"] is False
    assert relecture.valider(lot, [{"transcrit": "Gary Iev", "graphie": "Gariaev", "francais": "Gariaev", "accepter": True},
                                   {"transcrit": "Zork Blim", "graphie": "", "francais": "", "accepter": False}]) == (1, 1)
    assert relecture.noms_en_attente(lot) == set()
    assert etapes.constater(t, video, ["fr"], "en", set())["validee"] is True
    assert [e["graphie"] for e in glossaire.charger(glossaire.chemin(lot))] == ["Gariaev"]
    faux2 = FauxTraducteur([])
    relecture.relire(CLAUDE, lot, t, "E", ["We saw Gary Iev and Zork Blim come."], journal=lambda _m: None, creer=lambda m, j: faux2)
    assert faux2.demandes == []                # nom écarté : plus jamais reproposé


def test_une_forme_francaise_ne_reecrit_pas_le_nom(tmp_path):
    t = tmp_path / "a"
    t.mkdir()
    faux = FauxTraducteur([[entree(0, statut="forme_francaise", graphie="Fingerprints of the Gods", francais="Les Empreintes des dieux")]])
    relecture.relire(CLAUDE, tmp_path / "lot.json", t, "E", ["We read Fingerprints twice."], journal=lambda _m: None, creer=lambda m, j: faux)
    assert relecture.propositions(tmp_path / "lot.json")[0]["graphie"] == "Fingerprints"


def test_panne_api_laisse_la_relecture_a_refaire(tmp_path):
    class Panne(FauxTraducteur):
        def completer(self, *a, **k):
            raise ValueError("panne")
    t = tmp_path / "a"
    t.mkdir()
    with pytest.raises(ValueError):
        relecture.relire(CLAUDE, tmp_path / "lot.json", t, "E", ["We saw Gary Iev come."], journal=lambda _m: None, creer=lambda m, j: Panne([]))
    assert not (t / relecture.FICHIER_VIDEO).exists()


def test_constater_lit_les_etapes_sur_le_disque(tmp_path):
    t, v = tmp_path / "t", tmp_path / "v.mp4"
    t.mkdir()
    e = etapes.constater(t, v, ["fr"], "en", set())
    assert not any(e.values())
    (t / "transcription.json").write_text("{}")
    (t / "traduction.fr.json").write_text("[]")
    etapes.fichier_doublage(t, "fr").parent.mkdir()
    etapes.fichier_doublage(t, "fr").write_bytes(b"x")
    e = etapes.constater(t, v, ["fr"], "en", set())
    assert e["transcrite"] and e["traduite"] and e["doublee"] and not e["assemblee"]
    etapes.chemin_mkv(v).write_bytes(b"x")
    assert etapes.constater(t, v, ["fr"], "en", set())["assemblee"]


def test_le_doublage_echappe_au_nettoyage(tmp_path):
    from soustitres import nettoyage
    f = etapes.fichier_doublage(tmp_path, "fr")
    f.parent.mkdir()
    f.write_bytes(b"x")
    nettoyage.nettoyer_dossier(tmp_path, {**nettoyage.DEFAUTS, "audio": True})
    assert f.exists()


def test_le_scan_ignore_les_mkv_assembles(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "a.multi.mkv").write_bytes(b"x")
    assert [v["nom"] for v in dossier.parcourir(tmp_path)] == ["a.mp4"]


# --- Phases du lot et « Continuer » ---------------------------------------------------------------------------

def gestionnaire(monkeypatch, tmp_path, avec_doublage=True):
    monkeypatch.setattr(taches, "FICHIER_DERNIER_LOT", tmp_path / "dernier_lot.json")
    d = {"actif": avec_doublage, "commande": ["python"], "modele": "m"}
    monkeypatch.setattr(config, "charger", lambda: {"traducteurs": [CLAUDE], "traducteur_actif": "claude", "sortie": str(tmp_path), "doublage": d})
    g = Gestionnaire.__new__(Gestionnaire)
    g.taches, g.ordre, g.lots, g.historique = {}, [], {}, []
    g.verrou, g.reveil = threading.Lock(), threading.Event()
    return g


def creer_lot(g, tmp_path, etats, **options):
    videos = [{"chemin": f"v{i}.mp4", "nom": f"v{i}.mp4", "etat": e, "tache": None, "erreur": ""} for i, e in enumerate(etats)]
    lot = Lot("r", videos, {"traducteur": "claude", **options}, tmp_path / "lot.json", "L")
    g.lots["L"] = lot
    return lot


def test_apres_la_traduction_le_lot_passe_au_doublage(tmp_path):
    lot = Lot("r", [{"chemin": "a", "etat": "traduit"}, {"chemin": "b", "etat": "fait"}], {}, tmp_path / "l.json", "L")
    lot.phase = 2
    lot.terminer_phase()
    assert (lot.phase, lot.etat) == (3, "en_cours")
    assert lot.prochaine()["chemin"] == "a"
    lot.videos[0]["etat"] = "fait"
    lot.terminer_phase()
    assert lot.etat == "termine"


def test_phase_doublage_une_video_a_la_fois_jusqu_a_larret(monkeypatch, tmp_path):
    g = gestionnaire(monkeypatch, tmp_path)
    lot = creer_lot(g, tmp_path, ["traduit", "traduit"], arret="assemblage")
    lot.phase = 3
    g._alimenter_lot(lot)
    assert [v["etat"] for v in lot.videos] == ["en_cours", "traduit"]
    assert g.taches[lot.videos[0]["tache"]].options["jusqua"] == "assemblage"


def test_video_traduite_attend_le_doublage_seulement_si_le_lot_le_demande(monkeypatch, tmp_path):
    g = gestionnaire(monkeypatch, tmp_path)
    t = Tache({"source": "v", "lot": "L"})
    assert g._reste_a_doubler(creer_lot(g, tmp_path, ["fait"], arret="doublage"), t) is True
    assert g._reste_a_doubler(creer_lot(g, tmp_path, ["fait"], arret="traduction"), t) is False
    assert g._reste_a_doubler(creer_lot(g, tmp_path, ["fait"]), t) is False


def test_continuer_reprend_la_tache_en_pause_jusquau_bout(monkeypatch, tmp_path):
    g = gestionnaire(monkeypatch, tmp_path)
    lot = creer_lot(g, tmp_path, ["prepare", "prepare"], arret="assemblage")
    lot.etat = "attente_traduction"
    t = Tache({"source": "v0.mp4", "lot": "L"})
    t.etat, t.prepare = "en_pause", SimpleNamespace(sous_titres=[1], code="en")
    g.taches[t.id] = t
    g.ordre.append(t.id)
    lot.videos[0]["tache"] = t.id
    assert g.lot_continuer("L", "v0.mp4")
    assert t.etat == "en_attente" and t.options["jusqua"] == "assemblage" and t.options["pause"] is False
    assert [v["etat"] for v in lot.videos] == ["en_cours", "prepare"]
    assert lot.etat == "attente_traduction"
    assert not g.lot_continuer("L", "v0.mp4")   # déjà en cours


def test_continuer_sans_doublage_sarrete_a_la_traduction(monkeypatch, tmp_path):
    g = gestionnaire(monkeypatch, tmp_path, avec_doublage=False)
    lot = creer_lot(g, tmp_path, ["a_faire"])
    assert g.lot_continuer("L", "v0.mp4")
    assert g.taches[lot.videos[0]["tache"]].options["jusqua"] == "traduction"


def test_lot_sous_titres_seuls_ne_lance_jamais_le_doublage(monkeypatch, tmp_path):
    g = gestionnaire(monkeypatch, tmp_path)
    lot = creer_lot(g, tmp_path, ["a_faire"], arret="traduction")
    assert g.lot_continuer("L", "v0.mp4")
    assert g.taches[lot.videos[0]["tache"]].options["jusqua"] == "traduction"


def test_objectif_doublage_apres_coup_ne_lance_rien(monkeypatch, tmp_path):
    g = gestionnaire(monkeypatch, tmp_path)
    lot = creer_lot(g, tmp_path, ["fait", "fait", "fait"], arret="traduction")
    lot.etat = "termine"
    lot.videos[0].update(traduite=True)
    lot.videos[1].update(traduite=True, assemblee=True)
    assert g.lot_objectif("L", "assemblage")
    assert [v["etat"] for v in lot.videos] == ["traduit", "fait", "fait"]
    assert lot.etat == "suspendu" and not g.taches
    assert g.lot_objectif("L", "traduction")
    assert [v["etat"] for v in lot.videos] == ["fait", "fait", "fait"]
    assert not g.lot_objectif("L", "n_importe_quoi")


def test_temps_de_doublage_annonce_seulement_si_demande(tmp_path):
    videos = [{"chemin": "a", "etat": "traduit", "duree": 1000.0}, {"chemin": "b", "etat": "fait", "duree": 1000.0, "assemblee": True}]
    lot = Lot("r", videos, {"arret": "traduction"}, tmp_path / "l.json", "L")
    assert lot.doublage_a_faire_s() == 0
    lot.options["arret"] = "assemblage"
    assert lot.doublage_a_faire_s() > 0


def test_recharger_un_lot_garde_les_videos_a_doubler(tmp_path):
    f = tmp_path / "lot.json"
    dossier.ecrire_lot(f, {"id": "L", "racine": "r", "options": {"arret": "assemblage"}, "etat": "en_cours", "videos": [{"chemin": "a", "etat": "traduit"}, {"chemin": "b", "etat": "en_cours"}]})
    assert [v["etat"] for v in Lot.depuis_fichier(f).videos] == ["traduit", "a_faire"]


def test_continuer_relance_un_lot_termine_pour_cette_video(monkeypatch, tmp_path):
    g = gestionnaire(monkeypatch, tmp_path)
    lot = creer_lot(g, tmp_path, ["fait", "fait"])
    lot.etat, lot.phase, lot.fin = "termine", 2, 123.0
    assert g.lot_continuer("L", "v1.mp4")
    assert (lot.etat, lot.phase, lot.fin) == ("en_cours", 3, None)
    assert g.taches[lot.videos[1]["tache"]].options["verifier"] is True


def test_le_doublage_nest_jamais_lance_en_parallele(monkeypatch, tmp_path):
    g = gestionnaire(monkeypatch, tmp_path)
    lot = creer_lot(g, tmp_path, ["en_cours"])
    lot.simultanees = 5
    t = Tache({"source": "v", "lot": "L", "jusqua": "assemblage"})
    t.prepare = SimpleNamespace(sous_titres=[], code="en")
    vus = []
    monkeypatch.setattr(g, "_executer", lambda x: vus.append(threading.current_thread()), raising=False)
    g._demarrer(t)
    assert vus == [threading.current_thread()]


def test_lot_recharge_garde_les_noms_a_valider(monkeypatch, tmp_path):
    g = gestionnaire(monkeypatch, tmp_path)
    lot = creer_lot(g, tmp_path, ["prepare"], relecture=True)
    relecture._ecrire(relecture.chemin(lot.fichier), {"examines": ["Gary Iev"], "propositions": [{"transcrit": "Gary Iev", "graphie": "Gariaev", "occurrences": 2}]})
    t = pipeline.dossier_travail(tmp_path, "v0.mp4")
    t.mkdir(parents=True)
    (t / relecture.FICHIER_VIDEO).write_text(json.dumps({"noms": ["Gary Iev"]}))
    g._constater(lot)
    assert lot.videos[0]["relue"] and not lot.videos[0]["validee"]
    assert [p["transcrit"] for p in g.propositions_lot("L", "v0.mp4")["propositions"]] == ["Gary Iev"]
    assert g.valider_noms("L", [{"transcrit": "Gary Iev", "graphie": "Gariaev", "francais": "", "accepter": True}]) == {"acceptes": 1, "ecartes": 0}
    assert lot.videos[0]["validee"]


# --- Assemblage -------------------------------------------------------------------------------------------------

INFOS = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}, {"index": 1, "codec_type": "audio", "codec_name": "aac", "tags": {"language": "und"}},
                     {"index": 2, "codec_type": "subtitle", "codec_name": "mov_text"}], "format": {"duration": "10.0"}}


def test_commande_garde_les_pistes_et_ajoute_le_doublage():
    c = assemblage.commande("ffmpeg", Path("v.mp4"), INFOS, Path("d.m4a"), "fr", "en", [("en", Path("v.en.srt")), ("fr", Path("v.fr.srt"))], Path("s.part"))
    t = " ".join(c)
    assert "-map 0:a" in t and "-map 1:a:0" in t and "-map 2:0" in t and "-map 3:0" in t
    assert "-c:v copy" in t and "-c:a copy" in t and "-c:s:0 srt" in t
    assert "language=eng" in t and "language=fre" in t and "title=Français (doublage IA)" in t
    assert "-c:a aac" not in t                 # la piste doublée est déjà encodée


def outils_presents():
    from shutil import which
    return which("ffmpeg") and which("ffprobe")


@pytest.mark.skipif(not outils_presents(), reason="ffmpeg absent")
def test_assemblage_reel_ne_touche_pas_a_loriginal(tmp_path):
    v, d = tmp_path / "v.mp4", tmp_path / "d.m4a"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=2", "-f", "lavfi", "-i", "sine=f=220:d=2", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(v)], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=2", "-c:a", "aac", str(d)], check=True)
    st = {c: tmp_path / f"v.{c}.srt" for c in ("en", "fr")}
    for f in st.values():
        f.write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
    avant = v.read_bytes()
    m = assemblage.assembler(v, d, "fr", "en", list(st.items()), journal=lambda _m: None)
    assert m.name == "v.multi.mkv" and v.read_bytes() == avant and not m.with_name(m.name + ".part").exists()
    infos = assemblage.sonde(m)
    assert [s["codec_type"] for s in infos["streams"]] == ["video", "audio", "audio", "subtitle", "subtitle"]
    assert [s["tags"].get("language") for s in infos["streams"] if s["codec_type"] == "subtitle"] == ["eng", "fre"]


def clip(tmp_path, nom="v.mp4"):
    v, d = tmp_path / nom, tmp_path / "d.m4a"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=2", "-f", "lavfi", "-i", "sine=f=220:d=2", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(v)], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=2", "-c:a", "aac", str(d)], check=True)
    st = tmp_path / f"{Path(nom).stem}.en.srt"
    st.write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
    return v, assemblage.assembler(v, d, "fr", "en", [("en", st)], journal=lambda _m: None)


@pytest.mark.skipif(not outils_presents(), reason="ffmpeg absent")
def test_remplacer_met_le_mkv_a_la_place_de_loriginal(monkeypatch, tmp_path):
    v, m = clip(tmp_path)
    jetes = []
    monkeypatch.setattr(assemblage, "corbeille", lambda f: (jetes.append(f), Path(f).unlink(), True)[2])
    final = assemblage.remplacer_original(v, m, 2, journal=lambda _m: None)
    assert final == tmp_path / "v.mkv" and final.exists() and jetes == [v] and not m.exists() and not v.exists()


@pytest.mark.skipif(not outils_presents(), reason="ffmpeg absent")
def test_sans_corbeille_tout_est_garde(monkeypatch, tmp_path):
    v, m = clip(tmp_path)
    monkeypatch.setattr(assemblage, "corbeille", lambda f: False)
    assert assemblage.remplacer_original(v, m, 2, journal=lambda _m: None) == m
    assert v.exists() and m.exists()


@pytest.mark.skipif(not outils_presents(), reason="ffmpeg absent")
def test_mkv_incomplet_ne_remplace_jamais(monkeypatch, tmp_path):
    v, m = clip(tmp_path)
    monkeypatch.setattr(assemblage, "corbeille", lambda f: pytest.fail("ne doit pas jeter l'original"))
    with pytest.raises(assemblage.ErreurAssemblage):
        assemblage.remplacer_original(v, m, 5, journal=lambda _m: None)
    assert v.exists() and m.exists()


@pytest.mark.skipif(not outils_presents(), reason="ffmpeg absent")
def test_nom_final_deja_pris_ne_remplace_pas(monkeypatch, tmp_path):
    v, m = clip(tmp_path)
    (tmp_path / "v.mkv").write_bytes(b"autre")
    monkeypatch.setattr(assemblage, "corbeille", lambda f: pytest.fail("ne doit pas jeter l'original"))
    assert assemblage.remplacer_original(v, m, 2, journal=lambda _m: None) == m
    assert (tmp_path / "v.mkv").read_bytes() == b"autre"


def test_video_remplacee_reste_comptee_comme_assemblee(tmp_path):
    t = tmp_path / "t"
    m = etapes.marque_integration(t)
    m.parent.mkdir(parents=True)
    m.write_text("x")
    assert etapes.constater(t, tmp_path / "v.mkv", ["fr"], "en", set())["assemblee"]


def test_remplacement_change_le_chemin_de_la_video_du_lot(monkeypatch, tmp_path):
    g = gestionnaire(monkeypatch, tmp_path)
    lot = creer_lot(g, tmp_path, ["en_cours"], arret="assemblage", remplacer=True)
    t = Tache({"source": "v0.mp4", "lot": "L", "jusqua": "assemblage"})
    t.etat, t.prepare, t.resultat = "terminee", SimpleNamespace(sous_titres=[1], code="en"), {"mkv": "D:\\x\\v0.mkv", "remplace": True}
    lot.videos[0]["tache"] = t.id
    g.taches[t.id] = t
    g._maj_lot(t)
    assert (lot.videos[0]["chemin"], lot.videos[0]["nom"], lot.videos[0]["etat"]) == ("D:/x/v0.mkv", "v0.mkv", "fait")


def test_objectif_sous_titres_annule_le_remplacement(monkeypatch, tmp_path):
    g = gestionnaire(monkeypatch, tmp_path)
    lot = creer_lot(g, tmp_path, ["fait"], arret="assemblage", remplacer=True)
    assert g.lot_objectif("L", "traduction", True)
    assert lot.options["remplacer"] is False


# --- Sous-titres corrigés à la main ---------------------------------------------------------------------------

def test_srt_regenere_remplace_seulement_ce_que_le_service_a_pose(tmp_path):
    a, c, e = tmp_path / "a.srt", tmp_path / "c.srt", {}
    a.write_text("v1", encoding="utf-8")
    pipeline.poser_srt(a, c, e, journal=lambda _m: None)
    assert c.read_text(encoding="utf-8") == "v1"
    a.write_text("v2", encoding="utf-8")
    pipeline.poser_srt(a, c, e, journal=lambda _m: None)
    assert c.read_text(encoding="utf-8") == "v2"


def test_srt_corrige_a_la_main_nest_pas_ecrase(tmp_path):
    a, c, e, vus = tmp_path / "a.srt", tmp_path / "c.srt", {}, []
    a.write_text("v1", encoding="utf-8")
    pipeline.poser_srt(a, c, e, journal=vus.append)
    c.write_text("corrigé", encoding="utf-8")
    a.write_text("v2", encoding="utf-8")
    pipeline.poser_srt(a, c, e, journal=vus.append)
    assert c.read_text(encoding="utf-8") == "corrigé" and "gardés" in vus[0]
    pipeline.poser_srt(a, c, e, refaire=True, journal=vus.append)
    assert c.read_text(encoding="utf-8") == "v2"


def test_srt_different_sans_empreinte_est_garde(tmp_path):
    a, c = tmp_path / "a.srt", tmp_path / "c.srt"
    a.write_text("nouveau", encoding="utf-8")
    c.write_text("ancien", encoding="utf-8")
    pipeline.poser_srt(a, c, {}, journal=lambda _m: None)
    assert c.read_text(encoding="utf-8") == "ancien"


# --- Lancement du doublage --------------------------------------------------------------------------------------

def test_chemin_pour_wsl():
    assert doublage.chemin_pour({"doublage": {"wsl": True}}, "D:/Vidéos/a b.mp4") == "/mnt/d/Vidéos/a b.mp4"
    assert doublage.chemin_pour({"doublage": {}}, "D:/a.mp4") == "D:/a.mp4"


def programme_factice(tmp_path, corps):
    f = tmp_path / "faux.py"
    f.write_text("import json, sys\nsys.stdout.reconfigure(encoding='utf-8')\nt = json.loads(sys.stdin.readline())\n" + corps, encoding="utf-8")
    return f


def cfg_doublage():
    return {"doublage": {"actif": True, "commande": [sys.executable], "modele": "m"}}


def test_doublage_journalise_et_produit_le_fichier(monkeypatch, tmp_path):
    p = programme_factice(tmp_path, "print('» Doublage : 1/1', flush=True)\nprint('bruit', flush=True)\nopen(t['sortie'], 'wb').write(b'x')\n")
    monkeypatch.setattr(doublage, "PROGRAMME", p)
    lignes = []
    r = doublage.doubler(cfg_doublage(), Path("v.mp4"), tmp_path, Path("a.srt"), Path("b.srt"), "fr", journal=lignes.append)
    assert r.read_bytes() == b"x" and lignes == ["Doublage : 1/1"]


def test_doublage_rapporte_lechec(monkeypatch, tmp_path):
    monkeypatch.setattr(doublage, "PROGRAMME", programme_factice(tmp_path, "print('CUDA out of memory', flush=True)\nsys.exit(3)\n"))
    with pytest.raises(doublage.ErreurDoublage, match="CUDA out of memory"):
        doublage.doubler(cfg_doublage(), Path("v.mp4"), tmp_path, Path("a.srt"), Path("b.srt"), "fr", journal=lambda _m: None)


def test_doublage_annule_arrete_le_programme(monkeypatch, tmp_path):
    p = programme_factice(tmp_path, "import time\nfor i in range(600):\n    print('tic', flush=True)\n    time.sleep(0.1)\nopen(t['sortie'], 'wb').write(b'x')\n")
    monkeypatch.setattr(doublage, "PROGRAMME", p)
    with pytest.raises(doublage.ErreurDoublage, match="annulée"):
        doublage.doubler(cfg_doublage(), Path("v.mp4"), tmp_path, Path("a.srt"), Path("b.srt"), "fr", journal=lambda _m: None, annule=lambda: True)
    assert not etapes.fichier_doublage(tmp_path, "fr").exists()


def test_doublage_deja_fait_nest_pas_refait(tmp_path):
    f = etapes.fichier_doublage(tmp_path, "fr")
    f.parent.mkdir()
    f.write_bytes(b"x")
    assert doublage.doubler({}, Path("v.mp4"), tmp_path, Path("a"), Path("b"), "fr", journal=lambda _m: None) == f
