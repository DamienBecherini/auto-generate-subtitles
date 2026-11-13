import threading

from soustitres import __main__ as cli, config, dossier, lot_commande
from soustitres.web import taches
from soustitres.web.lots import Lot


def gestionnaire(tmp_path, monkeypatch, executer):
    """Gestionnaire dont la boucle tourne, mais où chaque tâche est simulée par `executer(t)` (rien n'est transcrit)."""
    monkeypatch.setattr(taches, "FICHIER_DERNIER_LOT", tmp_path / "dernier_lot.json")
    g = taches.Gestionnaire.__new__(taches.Gestionnaire)
    g.taches, g.ordre, g.lots, g.historique = {}, [], {}, []
    g.verrou, g.reveil = threading.Lock(), threading.Event()

    def simule(t):
        t.etat = "en_cours"
        executer(t)
        g._maj_lot(t)
    monkeypatch.setattr(g, "_executer", simule)
    threading.Thread(target=g._boucle, daemon=True).start()
    return g


def preparer(tmp_path, monkeypatch, noms=("a.mp4", "b.mp4")):
    videos = tmp_path / "videos"
    videos.mkdir()
    for n in noms:
        (videos / n).write_bytes(b"")
    cfg = {"sortie": str(tmp_path / "sortie"), "langues_cibles": ["fr"], "destination": {"a_cote": True, "sortie": False}}
    monkeypatch.setattr(config, "charger", lambda: cfg)
    return videos, cfg


def terminee(t):
    t.ecrire("fini : " + t.options["source"])
    t.etat = "terminee"


def test_dossier_cree_un_lot_et_le_mene_au_bout(tmp_path, monkeypatch, capsys):
    videos, cfg = preparer(tmp_path, monkeypatch)
    g = gestionnaire(tmp_path, monkeypatch, terminee)
    lignes = []
    code = lot_commande.executer(str(videos), cfg, lignes.append, langues=["fr", "de"], gestionnaire=g)
    assert code == 0
    lot = dossier.lire_lot(videos / dossier.NOM_LOT)
    assert lot["etat"] == "termine" and [v["etat"] for v in lot["videos"]] == ["fait", "fait"]
    assert lot["options"]["pause"] is False and lot["options"]["langues"] == ["fr", "de"]
    assert any("1/2 : a.mp4" in x for x in lignes) and "fini : " in capsys.readouterr().out


def test_dossier_avec_lot_existant_le_reprend(tmp_path, monkeypatch):
    videos, cfg = preparer(tmp_path, monkeypatch)
    a, b = (str(videos / n).replace("\\", "/") for n in ("a.mp4", "b.mp4"))
    ancien = Lot(str(videos), [{"chemin": a, "nom": "a.mp4", "langue": "", "etat": "fait", "tache": "x", "nb": 3, "langue_detectee": "en", "erreur": ""},
                               {"chemin": b, "nom": "b.mp4", "langue": "", "etat": "en_cours", "tache": "y", "nb": None, "langue_detectee": "", "erreur": ""}],
                 {"traducteur": "local", "pause": True}, videos / dossier.NOM_LOT)
    ancien.enregistrer()
    lancees = []
    g = gestionnaire(tmp_path, monkeypatch, lambda t: (lancees.append(t.options["source"]), terminee(t)))
    assert lot_commande.executer(str(videos), cfg, lambda _m: None, traducteur="claude", gestionnaire=g) == 0
    assert lancees == [b]                                     # la vidéo faite est sautée
    lot = dossier.lire_lot(videos / dossier.NOM_LOT)
    assert lot["id"] == ancien.id and lot["options"]["traducteur"] == "claude" and lot["options"]["pause"] is False


def test_erreur_d_une_video_donne_un_code_1(tmp_path, monkeypatch):
    videos, cfg = preparer(tmp_path, monkeypatch)

    def echoue_sur_b(t):
        if t.options["source"].endswith("b.mp4"):
            t.etat, t.erreur = "erreur", "serveur injoignable"
        else:
            terminee(t)
    g = gestionnaire(tmp_path, monkeypatch, echoue_sur_b)
    lignes = []
    assert lot_commande.executer(str(videos), cfg, lignes.append, gestionnaire=g) == 1
    assert any("b.mp4 : serveur injoignable" in x for x in lignes)


def test_lot_d_un_autre_dossier_dans_la_sortie_ignore(tmp_path, monkeypatch):
    videos, cfg = preparer(tmp_path, monkeypatch)
    autre = Lot(str(tmp_path / "ailleurs"), [], {}, tmp_path / "sortie" / dossier.NOM_LOT)
    autre.enregistrer()
    assert lot_commande.trouver_fichier_lot(videos, cfg, None) is None
    assert lot_commande.trouver_fichier_lot(autre.fichier, cfg, None) == autre.fichier


def test_la_commande_envoie_un_dossier_vers_le_lot(tmp_path, monkeypatch):
    videos, _ = preparer(tmp_path, monkeypatch)
    recu = {}
    monkeypatch.setattr(lot_commande, "executer", lambda chemin, cfg, journal, **o: recu.update(o, chemin=chemin) or 0)
    assert cli.main([str(videos), "--langues", "fr,en", "--jusqua", "assemblage", "--sans-sous-dossiers"]) == 0
    assert recu["chemin"] == str(videos) and recu["langues"] == ["fr", "en"] and recu["arret"] == "assemblage" and recu["recursif"] is False


def test_resume_du_lot_donne_l_etape_de_la_video_en_cours(tmp_path):
    lot = Lot("D:/", [{"chemin": "D:/a.mp4", "nom": "a.mp4", "etat": "en_cours", "tache": "t1"}], {}, tmp_path / dossier.NOM_LOT)
    assert lot.resume()["etape"] == ""                        # pas de gestionnaire branché
    lot.etape_de = {"t1": "traduction"}.get
    assert lot.phase == 1 and lot.resume()["etape"] == "traduction"
