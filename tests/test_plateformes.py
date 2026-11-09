"""Boîtes de dialogue et ouverture de dossier hors Windows : commandes construites selon le système et les outils présents."""
import pytest

from soustitres.web import serveur


def avec(*outils):
    return lambda nom: f"/usr/bin/{nom}" if nom in outils else None


def test_zenity_fichier_video_avec_filtre_et_depart():
    c = serveur.commande_dialogue("fichier", "/data/videos", "linux", avec("zenity"))
    assert c[:2] == ["zenity", "--file-selection"]
    assert "--filename=/data/videos/" in c
    assert any(a.startswith("--file-filter=Vidéos") and "*.mkv" in a for a in c)
    assert "--directory" not in c


def test_zenity_dossier():
    c = serveur.commande_dialogue("dossier", "", "linux", avec("zenity", "kdialog"))
    assert c[0] == "zenity" and "--directory" in c and not any(a.startswith("--filename") for a in c)


def test_kdialog_si_pas_de_zenity():
    assert serveur.commande_dialogue("lot", "/x", "linux", avec("kdialog"))[3:] == ["--getopenfilename", "/x", "*.lot.json|Fichier de lot"]
    assert serveur.commande_dialogue("dossier_source", "", "linux", avec("kdialog"))[3:] == ["--getexistingdirectory", "."]


def test_aucun_outil():
    assert serveur.commande_dialogue("fichier", "", "linux", avec()) is None


def test_macos_chemin_passe_en_argument_pas_dans_le_script():
    depart = '/a"; do shell script "x'
    c = serveur.commande_dialogue("fichier", depart, "darwin", avec())
    assert c[:2] == ["osascript", "-e"] and "choose file" in c[2]
    assert depart not in c[2] and c[-1] == depart
    assert "choose folder" in serveur.commande_dialogue("dossier", "", "darwin", avec())[2]


def test_sans_outil_message_utile(monkeypatch):
    monkeypatch.setattr(serveur, "commande_dialogue", lambda *_: None)
    with pytest.raises(serveur.ErreurRequete, match="saisir le chemin"):
        serveur._dialogue_unix("fichier", "")


@pytest.mark.parametrize("code, sortie, attendu", [(0, "/data/film.mp4\n", "/data/film.mp4"), (0, "/data/dossier/\n", "/data/dossier"), (1, "", "")])
def test_resultat_du_dialogue(monkeypatch, code, sortie, attendu):
    monkeypatch.setattr(serveur, "commande_dialogue", lambda *_: ["outil"])
    monkeypatch.setattr(serveur.subprocess, "run", lambda *_a, **_k: type("R", (), {"returncode": code, "stdout": sortie})())
    assert serveur._dialogue_unix("fichier", "") == attendu


def test_ouvrir_selon_le_systeme(tmp_path):
    f = tmp_path / "a.srt"
    f.write_text("x", encoding="utf-8")
    assert serveur.commande_ouvrir(f, "win32") == ["explorer", "/select,", str(f)]
    assert serveur.commande_ouvrir(tmp_path, "win32") == ["explorer", str(tmp_path)]
    assert serveur.commande_ouvrir(f, "darwin") == ["open", "-R", str(f)]
    assert serveur.commande_ouvrir(tmp_path, "darwin") == ["open", str(tmp_path)]
    assert serveur.commande_ouvrir(f, "linux") == ["xdg-open", str(tmp_path)]
