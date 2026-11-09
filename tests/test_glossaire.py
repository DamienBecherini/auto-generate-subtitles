"""Glossaire validé : correction exacte des mots, temps conservés, consignes de traduction."""
import json

from soustitres import glossaire, traduction

E = [{"transcrit": "Tajevsky", "graphie": "Chizhevsky", "francais": "Tchijevski"},
     {"transcrit": "Corey Good", "graphie": "Corey Goode", "francais": ""},
     {"transcrit": "Padma Sambhava", "graphie": "Padmasambhava", "francais": ""},
     {"transcrit": "Plato", "graphie": "Plato", "francais": "Platon"}]


def m(*mots):
    return [{"word": w, "start": float(i), "end": i + 0.5} for i, w in enumerate(mots)]


def textes(mots):
    return [x["word"] for x in mots]


def test_corrige_un_mot_et_garde_ponctuation_et_temps():
    r, n = glossaire.corriger_mots(m("scientist", "Tajevsky,", "was"), E)
    assert textes(r) == ["scientist", "Chizhevsky,", "was"] and n == 1
    assert (r[1]["start"], r[1]["end"]) == (1.0, 1.5)


def test_garde_le_possessif():
    assert textes(glossaire.corriger_mots(m("Tajevsky's", "work"), E)[0]) == ["Chizhevsky's", "work"]


def test_plusieurs_mots_meme_nombre():
    r, _ = glossaire.corriger_mots(m("with", "Corey", "Good."), E)
    assert textes(r) == ["with", "Corey", "Goode."]


def test_plusieurs_mots_fusionnes_couvrent_tout_le_temps():
    r, n = glossaire.corriger_mots(m("by", "Padma", "Sambhava!"), E)
    assert textes(r) == ["by", "Padmasambhava!"] and n == 1
    assert (r[1]["start"], r[1]["end"]) == (1.0, 2.5)


def test_mot_exact_seulement():
    assert glossaire.corriger_mots(m("Tajevskyan", "tajevsky", "Good"), E) == (m("Tajevskyan", "tajevsky", "Good"), 0)


def test_mots_originaux_non_modifies():
    d = m("Tajevsky")
    glossaire.corriger_mots(d, E)
    assert d[0]["word"] == "Tajevsky"


def test_sans_glossaire_rien_ne_change(tmp_path):
    assert glossaire.charger(tmp_path / "absent.json") == [] and glossaire.charger(None) == []
    (tmp_path / "x.json").write_text("{cassé", encoding="utf-8")
    assert glossaire.charger(tmp_path / "x.json") == []
    d = m("Tajevsky")
    assert glossaire.corriger_mots(d, []) == (d, 0)


def test_charger_lit_les_entrees(tmp_path):
    f = tmp_path / glossaire.NOM_FICHIER
    f.write_text(json.dumps(E), encoding="utf-8")
    assert len(glossaire.charger(f)) == 4


def test_termes_seulement_ceux_de_la_video():
    t = glossaire.termes_pour(E, ["We read Chizhevsky and Plato today"], "fr")
    assert t == ["Chizhevsky → Tchijevski", "Plato → Platon"]


def test_termes_autre_langue_sans_forme_francaise():
    assert glossaire.termes_pour(E, ["Chizhevsky"], "de") == ["Chizhevsky"]


def test_consigne_contient_les_noms():
    c = traduction.consigne("fr", ["Chizhevsky → Tchijevski"])
    assert "Chizhevsky → Tchijevski" in c
    assert "Noms à respecter" not in traduction.consigne("fr")


def test_remplacer_texte_mot_entier_le_plus_long_d_abord():
    t, n = glossaire.remplacer_texte("Corey Good et Goody, Tajevsky.", [("Good", "X"), ("Corey Good", "Corey Goode"), ("Tajevsky", "Tchijevski")])
    assert t == "Corey Goode et Goody, Tchijevski." and n == 2
