import re
import tomllib

import pytest

from soustitres import config


def test_reference_env(monkeypatch):
    monkeypatch.setenv("MA_CLE", "valeur")
    assert config.resoudre_cle("env:MA_CLE") == "valeur"


def test_reference_fichier(tmp_path):
    f = tmp_path / "cle.txt"
    f.write_text("  valeur\n", encoding="utf-8")
    assert config.resoudre_cle(f"file:{f}") == "valeur"


def test_fichier_absent_sans_afficher_de_contenu(tmp_path):
    with pytest.raises(config.ErreurConfig, match="illisible"):
        config.resoudre_cle(f"file:{tmp_path / 'absent'}")


def test_sops_absent_propose_les_autres_formes(monkeypatch):
    monkeypatch.setattr(config.shutil, "which", lambda _: None)
    with pytest.raises(config.ErreurConfig, match="env:<NOM> ou file:<chemin>"):
        config.resoudre_cle("sops:f.env#NOM")


def test_cle_en_clair_refusee():
    with pytest.raises(config.ErreurConfig, match="en clair"):
        config.resoudre_cle("sk-valeur-en-clair")


@pytest.mark.parametrize("cle", ["env:X", "file:/chemin/cle", "sops:f.env#X", ""])
def test_references_acceptees_a_l_enregistrement(tmp_path, cle):
    config.enregistrer({"traducteurs": [{"id": "a", "cle": cle}]}, tmp_path / "c.json")


def test_cle_en_clair_refusee_a_l_enregistrement(tmp_path):
    with pytest.raises(config.ErreurConfig, match="jamais la valeur"):
        config.enregistrer({"traducteurs": [{"id": "a", "cle": "valeur"}]}, tmp_path / "c.json")
    assert not (tmp_path / "c.json").exists()


def test_le_dossier_de_sortie_part_du_dossier_personnel(tmp_path):
    defauts = tmp_path / "d.toml"
    defauts.write_text('sortie = "~/Videos"\n', encoding="utf-8")
    assert "~" not in config.charger(defauts, None)["sortie"]


def test_les_reglages_utilisateur_recouvrent_les_valeurs_par_defaut(tmp_path):
    defauts, perso = tmp_path / "d.toml", tmp_path / "c.json"
    defauts.write_text('sortie = "/a"\n[web]\nport = 1\nx = 2\n', encoding="utf-8")
    perso.write_text('{"sortie": "/b", "web": {"port": 9}}', encoding="utf-8")
    cfg = config.charger(defauts, perso)
    assert (cfg["sortie"], cfg["web"]) == ("/b", {"port": 9, "x": 2})


def test_valeurs_par_defaut_sans_information_personnelle():
    texte = config.FICHIER_DEFAUTS.read_text(encoding="utf-8")
    for motif in (r"\b[A-Z]:/", r"192\.168\.", r"\bCT \d+", r"Damien(?!Becherini)", "homelab", "Webdev", r"sops:[^<]"):
        assert not re.search(motif, texte), motif


def test_valeurs_par_defaut_coherentes():
    with open(config.FICHIER_DEFAUTS, "rb") as f:
        cfg = tomllib.load(f)
    assert config.moteur(cfg, "transcriptions", "transcription_active")["id"] == "exemple"
    assert config.moteur(cfg, "traducteurs", "traducteur_actif")["type"] == "aucun"
    assert cfg["youtube"]["commande"] == ["yt-dlp"]
    assert not any("supervision" in m for m in cfg["traducteurs"])
