from soustitres import dossier


def arbre(tmp_path):
    for s in ("Season 2", "Season 10"):
        (tmp_path / s).mkdir()
        (tmp_path / s / "E01.mp4").write_bytes(b"x")
    (tmp_path / "Season 2" / "E01.fr.srt").write_text("1", encoding="utf-8")


def test_parcourir_tri_naturel_et_sous_titres(tmp_path):
    arbre(tmp_path)
    v = dossier.parcourir(tmp_path)
    assert [x["dossier"] for x in v] == ["Season 2", "Season 10"]
    assert v[0]["sous_titres"] == ["fr"] and v[1]["sous_titres"] == []


def test_analyse_complete(tmp_path, monkeypatch):
    arbre(tmp_path)
    monkeypatch.setattr(dossier, "langue_piste", lambda p: "en")
    a = dossier.Analyse(tmp_path)
    a.executer()
    d = a.vers_dict(True)
    assert d["etat"] == "fini" and d["nb"] == 2 and d["faites"] == 2 and len(d["videos"]) == 2
    assert set(d["langues"].values()) == {"en"} and not d["langues_interrompues"]


def test_liste_absente_du_suivi_sans_demande(tmp_path, monkeypatch):
    arbre(tmp_path)
    monkeypatch.setattr(dossier, "langue_piste", lambda p: "")
    a = dossier.Analyse(tmp_path)
    a.executer()
    assert "videos" not in a.vers_dict() and a.vers_dict()["langues"] == {}


def test_annuler_pendant_le_parcours(tmp_path):
    arbre(tmp_path)
    a = dossier.Analyse(tmp_path)
    a.annuler()
    a.executer()
    assert a.vers_dict()["etat"] == "annulee" and a.videos is None


def test_annuler_pendant_les_langues_garde_la_liste(tmp_path, monkeypatch):
    arbre(tmp_path)
    a = dossier.Analyse(tmp_path, ouvriers=1)

    def lire(p):
        a.annuler()
        return "en"

    monkeypatch.setattr(dossier, "langue_piste", lire)
    a.executer()
    d = a.vers_dict(True)
    assert d["etat"] == "fini" and d["nb"] == 2 and d["faites"] == 1 and d["langues_interrompues"]


def test_lot_existant_signale(tmp_path):
    (tmp_path / dossier.NOM_LOT).write_text("{}", encoding="utf-8")
    assert dossier.Analyse(tmp_path).lot_existant.endswith(dossier.NOM_LOT)
