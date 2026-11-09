from soustitres import decoupe
from soustitres.decoupe import Mot, Reglages, SousTitre


def mots(phrase: str, debut=0.0, pas=0.3):
    return [Mot(m, round(debut + i * pas, 3), round(debut + i * pas + pas * 0.8, 3)) for i, m in enumerate(phrase.split())]


def test_phrases_coupees_sur_ponctuation_et_pause():
    m = mots("Bonjour à tous. Aujourd'hui on parle") + mots("de sous-titres automatiques", debut=10.0)
    p = decoupe.phrases(m, Reglages())
    assert [" ".join(x.texte for x in ph) for ph in p] == ["Bonjour à tous.", "Aujourd'hui on parle", "de sous-titres automatiques"]


def test_phrase_longue_decoupee_en_blocs_lisibles():
    texte = ("Alors aujourd'hui nous allons voir comment générer des sous-titres automatiquement, "
             "en transcrivant l'audio puis en traduisant chaque ligne sans perdre la synchronisation")
    r = Reglages()
    st = decoupe.construire(mots(texte), r)
    assert len(st) >= 2
    for s in st:
        assert len(s.texte) <= r.caracteres
        assert s.fin - s.debut <= r.duree_max + 2.5   # durée de lecture ajoutée au plus
    assert " ".join(s.texte for s in st) == texte
    # coupe après la virgule
    assert any(s.texte.endswith("automatiquement,") for s in st)


def test_pas_de_chevauchement_et_duree_minimale():
    m = mots("Oui. Non. Peut-être bien que oui finalement.", pas=0.25)
    st = decoupe.construire(m, Reglages(duree_min=1.0, ecart_min=0.08))
    for a, b in zip(st, st[1:]):
        assert b.debut >= a.fin + 0.08 - 1e-9
    assert all(s.fin > s.debut for s in st)
    assert st[-1].fin - st[-1].debut >= 1.0


def test_couper_lignes_equilibre_et_ponctuation():
    r = Reglages(caracteres_par_ligne=42)
    l = decoupe.couper_lignes("Ceci est une première phrase assez longue, et voici la suite du propos", r)
    assert len(l) == 2 and all(len(x) <= 42 for x in l)
    assert l[0].endswith(",")
    assert decoupe.couper_lignes("Court", r) == ["Court"]


def test_scinder_trop_longs_repartit_le_temps():
    long_texte = "mot " * 40
    st = decoupe.scinder_trop_longs([SousTitre(0.0, 8.0, long_texte.strip())], Reglages())
    assert len(st) >= 2
    assert st[0].debut == 0.0 and st[-1].fin == 8.0
    assert all(len(s.texte) <= Reglages().caracteres for s in st)
    for a, b in zip(st, st[1:]):
        assert b.debut >= a.fin


def test_depuis_json_ignore_les_mots_vides():
    m = decoupe.depuis_json([{"word": " Hello", "start": 0, "end": 0.5}, {"word": "  ", "start": 0.5, "end": 0.6}])
    assert [x.texte for x in m] == ["Hello"]
