import os
import time

from soustitres import langue, nettoyage, traduction


def _fichiers(travail):
    noms = ["v.audio.m4a", "v.en.srt", "v.fr.srt", "v.fr.vtt", "transcription.json", "traduction.fr.json", "traduction.json",
            "journal-20261101-1000-a1.txt", "journal-20261108-1000-b2.txt"]
    for n in noms:
        (travail / n).write_text("x" * 10, encoding="utf-8")
    return noms


def test_classer(tmp_path):
    _fichiers(tmp_path)
    c = {k: sorted(p.name for p in v) for k, v in nettoyage.classer(tmp_path).items()}
    assert c["audio"] == ["v.audio.m4a"]
    assert c["sous_titres"] == ["v.en.srt", "v.fr.srt", "v.fr.vtt"]
    assert c["transcription"] == ["traduction.fr.json", "traduction.json", "transcription.json"]
    assert c["journaux"] == ["journal-20261101-1000-a1.txt", "journal-20261108-1000-b2.txt"]


def test_nettoyer_dossier_par_defaut_garde_transcription_et_journaux(tmp_path):
    _fichiers(tmp_path)
    nb, octets = nettoyage.nettoyer_dossier(tmp_path, nettoyage.DEFAUTS)
    assert nb == 4 and octets == 40
    restants = sorted(p.name for p in tmp_path.iterdir())
    assert restants == ["journal-20261101-1000-a1.txt", "journal-20261108-1000-b2.txt", "traduction.fr.json", "traduction.json", "transcription.json"]


def test_journaux_vieux_seulement(tmp_path):
    _fichiers(tmp_path)
    vieux = tmp_path / "journal-20261101-1000-a1.txt"
    il_y_a_3_jours = time.time() - 72 * 3600
    os.utime(vieux, (il_y_a_3_jours, il_y_a_3_jours))
    opts = {**nettoyage.DEFAUTS, "audio": False, "sous_titres": False, "journaux": True, "journaux_age_h": 48}
    nb, _ = nettoyage.nettoyer_dossier(tmp_path, opts)
    assert nb == 1 and not vieux.exists() and (tmp_path / "journal-20261108-1000-b2.txt").exists()


def test_garder_protege_un_fichier(tmp_path):
    _fichiers(tmp_path)
    nettoyage.nettoyer_dossier(tmp_path, nettoyage.DEFAUTS, garder={tmp_path / "v.fr.srt"})
    assert (tmp_path / "v.fr.srt").exists() and not (tmp_path / "v.en.srt").exists()


def test_bilan_et_nettoyer_tout(tmp_path):
    racine = tmp_path / ".travail"
    for d in ("a", "b"):
        (racine / d).mkdir(parents=True)
        _fichiers(racine / d)
    b = nettoyage.bilan(racine)
    assert b["audio"] == {"fichiers": 2, "octets": 20} and b["sous_titres"]["fichiers"] == 6
    r = nettoyage.nettoyer_tout(racine, {**nettoyage.DEFAUTS, "transcription": True, "journaux": True, "journaux_age_h": 0})
    assert r["audio"]["fichiers"] == 2 and r["transcription"] == {"fichiers": 6, "octets": 60} and r["journaux"]["fichiers"] == 4
    assert not racine.exists() or not any(racine.iterdir())   # dossiers vides supprimés


def test_purger_journaux(tmp_path):
    racine = tmp_path / ".travail"
    (racine / "a").mkdir(parents=True)
    _fichiers(racine / "a")
    vieux = racine / "a" / "journal-20261101-1000-a1.txt"
    t = time.time() - 100 * 3600
    os.utime(vieux, (t, t))
    assert nettoyage.purger_journaux(racine, 48) == (1, 10)
    assert nettoyage.purger_journaux(racine, 48) == (0, 0)


def test_langues_noms_et_codes():
    assert langue.nom("fr") == "français" and langue.nom("en") == "anglais"
    assert "zz" in langue.nom("zz")
    assert langue.normaliser_codes("fr, EN;de fr") == ["fr", "en", "de"]
    assert langue.normaliser_codes(["", " It "]) == ["it"]


def test_consigne_par_langue():
    assert "Tu traduis en français." in traduction.consigne("fr")
    assert "Tu traduis en allemand." in traduction.consigne("de")
    assert "{langue}" not in traduction.consigne("pl")
