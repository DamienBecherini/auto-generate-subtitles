from pathlib import Path

import pytest

from soustitres import dossier, source
from soustitres.web.lots import Lot


def test_code_langue_fichier():
    f = dossier.code_langue_fichier
    assert f("Film", Path("Film.fr.srt")) == "fr"
    assert f("Film", Path("Film.pt-BR.vtt")) == "pt"
    assert f("Film", Path("Film.eng.srt")) == "eng"
    assert f("Film", Path("Film.srt")) == "?"
    assert f("Film", Path("Film 2.fr.srt")) is None
    assert f("Film", Path("Film.fr.txt")) is None
    assert f("Film", Path("Film.forced.srt")) is None


def test_inventaire(tmp_path):
    (tmp_path / "S01").mkdir()
    (tmp_path / "S01" / "E01.mp4").write_bytes(b"x")
    (tmp_path / "S01" / "E01.en.srt").write_text("1", encoding="utf-8")
    (tmp_path / "S01" / "E01.fr.srt").write_text("1", encoding="utf-8")
    (tmp_path / "S01" / "E02.mkv").write_bytes(b"xx")
    (tmp_path / "Film.mp4").write_bytes(b"xxx")
    (tmp_path / "notes.txt").write_text("", encoding="utf-8")
    (tmp_path / ".travail" / "x").mkdir(parents=True)
    (tmp_path / ".travail" / "x" / "ignore.mp4").write_bytes(b"x")
    inv = dossier.inventaire(tmp_path, avec_langue_piste=False)
    assert inv["nb"] == 3
    noms = [(v["dossier"], v["nom"], v["sous_titres"]) for v in inv["videos"]]
    assert noms == [("", "Film.mp4", []), ("S01", "E01.mp4", ["en", "fr"]), ("S01", "E02.mkv", [])]
    assert dossier.inventaire(tmp_path, recursif=False, avec_langue_piste=False)["nb"] == 1
    with pytest.raises(source.ErreurSource):
        dossier.inventaire(tmp_path / "absent")


def test_fichier_de_lot(tmp_path):
    assert dossier.chemin_lot(Path("D:/src"), Path("D:/out"), True) == Path("D:/out") / dossier.NOM_LOT
    assert dossier.chemin_lot(Path("D:/src"), Path("D:/out"), False) == Path("D:/src") / dossier.NOM_LOT
    f = tmp_path / dossier.NOM_LOT
    videos = [{"chemin": "D:/a.mp4", "nom": "a.mp4", "langue": "en", "etat": "fait", "tache": "t1", "nb": 10, "langue_detectee": "en", "erreur": ""},
              {"chemin": "D:/b.mp4", "nom": "b.mp4", "langue": "", "etat": "prepare", "tache": "t2", "nb": 5, "langue_detectee": "en", "erreur": ""},
              {"chemin": "D:/c.mp4", "nom": "c.mp4", "langue": "", "etat": "erreur", "tache": "t3", "nb": None, "langue_detectee": "", "erreur": "x"}]
    lot = Lot("D:/", videos, {"langues": ["fr"], "pause": True}, f)
    lot.phase, lot.etat = 2, "en_cours"
    lot.enregistrer()
    assert f.exists()
    relu = Lot.depuis_fichier(f)
    assert relu.id == lot.id and relu.etat == "suspendu" and relu.phase == 1 and relu.options == {"langues": ["fr"], "pause": True}
    assert [v["etat"] for v in relu.videos] == ["fait", "a_faire", "a_faire"]
    assert relu.videos[1]["tache"] is None and relu.videos[0]["tache"] == "t1"
    assert relu.prochaine()["chemin"] == "D:/b.mp4"
    r = relu.resume()
    assert r["comptes"]["fait"] == 1 and r["comptes"]["a_faire"] == 2 and r["total"] == 3
    with pytest.raises(source.ErreurSource):
        dossier.lire_lot(tmp_path / "absent.json")
