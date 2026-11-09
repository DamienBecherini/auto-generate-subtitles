from soustitres import estimation, pipeline

CFG = {
    "traduction": {"lot": 25},
    "traducteurs": [
        {"id": "local", "type": "openai", "nom": "IA locale", "url": "http://127.0.0.1:8080"},
        {"id": "claude", "type": "claude", "nom": "Claude", "modele": "claude-sonnet-5-5"},
        {"id": "opus", "type": "claude", "nom": "Opus daté", "modele": "claude-opus-5-5-20261001"},
        {"id": "inconnu", "type": "claude", "nom": "?", "modele": "claude-futur-9"},
        {"id": "distant", "type": "openai", "nom": "Mistral", "url": "https://api.mistral.ai"},
        {"id": "aucun", "type": "aucun", "nom": "Aucun"},
    ],
}


def test_estimer_une_langue():
    e = estimation.estimer(CFG, 499, ["fr"], "en")
    assert e["langues"] == ["fr"] and e["requetes"] == 20
    assert e["jetons_entree"] == 499 * 36 and e["jetons_sortie"] == 499 * 23
    par_id = {t["id"]: t for t in e["traducteurs"]}
    assert abs(par_id["claude"]["cout_usd"] - (499 * 36 * 2 + 499 * 23 * 10) / 1e6) < 1e-4   # ≈ 0,15 $ (arrondi à 4 décimales)
    assert par_id["opus"]["cout_usd"] > par_id["claude"]["cout_usd"]                           # préfixe du modèle reconnu
    assert par_id["inconnu"]["cout_usd"] is None
    assert par_id["local"]["cout_usd"] == 0.0 and par_id["local"]["duree_s"] == round(499 * 0.6)
    assert par_id["distant"]["cout_usd"] is None
    assert par_id["aucun"]["cout_usd"] == 0.0


def test_langue_source_sautee_et_plusieurs_langues():
    e = estimation.estimer(CFG, 100, ["en", "fr", "de"], "en")
    assert e["langues"] == ["fr", "de"] and e["requetes"] == 8 and e["jetons_entree"] == 200 * 36
    assert estimation.estimer(CFG, 100, ["en"], "en")["requetes"] == 0


def test_message_de_transcription():
    assert "environ 4 min 10 s pour 30 min 00 s d'audio (« Serveur », 7.2 s" in pipeline.message_transcription({"nom": "Serveur", "vitesse": 7.2}, 1800)
    assert "environ 3 min 00 s" in pipeline.message_transcription({"nom": "PC", "vitesse": "10"}, 1800)   # chaîne saisie dans Réglages
    for m in ({"nom": "X"}, {"nom": "X", "vitesse": "?"}, {"nom": "X", "vitesse": 0}):
        assert "non renseignée" in pipeline.message_transcription(m, 1800)
    assert pipeline.texte_duree(3725) == "1 h 02 min" and pipeline.texte_duree(42) == "42 s"


def test_delai_de_transcription():
    assert pipeline.delai_transcription({"vitesse": 7.2}, 1800) == 1000
    assert pipeline.delai_transcription({"vitesse": 16}, 60) == 300       # plancher
    assert pipeline.delai_transcription({}, 1800) == 7200


def test_pas_de_reponse_du_serveur_leve_delai_depasse(tmp_path):
    import socket

    import pytest

    from soustitres import transcription
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"x")
    with socket.socket() as s:                  # accepte la connexion et ne répond jamais
        s.bind(("127.0.0.1", 0))
        s.listen()
        with pytest.raises(transcription.DelaiDepasse):
            transcription.transcrire(f"http://127.0.0.1:{s.getsockname()[1]}", audio, delai_s=1)


def test_pause_par_defaut():
    assert not estimation.pause_par_defaut({"type": "openai", "url": "http://127.0.0.1:8080"})
    assert not estimation.pause_par_defaut({"type": "aucun"})
    assert estimation.pause_par_defaut({"type": "claude"})
    assert estimation.pause_par_defaut({"type": "openai", "url": "https://api.openai.com"})


def test_prix_recouverts_par_la_config():
    cfg = {**CFG, "estimation": {"prix": {"claude-sonnet-5-5": [1.0, 5.0]}, "jetons_entree_par_ligne": 10, "jetons_sortie_par_ligne": 10}}
    e = estimation.estimer(cfg, 1000, ["fr"], "en")
    assert e["jetons_entree"] == 10000
    assert {t["id"]: t for t in e["traducteurs"]}["claude"]["cout_usd"] == 0.06
    assert estimation.prix_modele(cfg, "claude-haiku-5-5") == (0.10, 0.50)      # les défauts restent


def test_texte():
    t = estimation.texte(estimation.estimer(CFG, 499, ["fr"], "en"), CFG)
    assert "20 requêtes" in t and "Claude (claude-sonnet-5-5) : ≈ 0.15 $" in t and "IA locale : ≈ 4 min 59 s (gratuit)" in t
    assert "Rien à traduire" in estimation.texte(estimation.estimer(CFG, 10, ["en"], "en"), CFG)
