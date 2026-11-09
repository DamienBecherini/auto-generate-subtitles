from soustitres import formats, langue, traduction
from soustitres.decoupe import Reglages, SousTitre


def test_hms():
    assert formats.hms(0) == "00:00:00,000"
    assert formats.hms(3661.5) == "01:01:01,500"
    assert formats.hms(59.9996, ".") == "00:01:00.000"


def test_srt_et_vtt():
    st = [SousTitre(0.0, 1.5, "Bonjour à tous"), SousTitre(2.0, 4.0, "Deuxième sous-titre")]
    s = formats.srt(st, Reglages())
    assert s.startswith("1\n00:00:00,000 --> 00:00:01,500\nBonjour à tous\n\n2\n00:00:02,000 --> 00:00:04,000\n")
    v = formats.vtt(st, Reglages())
    assert v.startswith("WEBVTT\n\n00:00:00.000 --> 00:00:01.500\nBonjour à tous\n")


def test_detection_langue():
    assert langue.detecter("Hello everyone and welcome back to the channel, today we are going to talk about it") == "en"
    assert langue.detecter("Bonjour à tous, aujourd'hui nous allons parler des sous-titres et de la traduction") == "fr"
    assert langue.detecter("xyz qrs") == ""


def test_analyser_reponse():
    r = traduction.analyser_reponse("1. Bonjour à tous\n2) Bienvenue\nsur la chaîne\n3 - Troisième", 3)
    assert r == {1: "Bonjour à tous", 2: "Bienvenue sur la chaîne", 3: "Troisième"}
    assert traduction.analyser_reponse("Voici :\n1. A\n2. ", 2) == {1: "A"}


class FauxTraducteur:
    def __init__(self, oublie=()):
        self.oublie = set(oublie)
        self.appels = 0

    def preparer(self):
        pass

    def terminer(self):
        pass

    def completer(self, consigne, demande):
        self.appels += 1
        bloc = demande.split("Lignes à traduire :\n", 1)[1].split("\n\n", 1)[0]
        sortie = []
        for l in bloc.splitlines():
            n, t = l.split(". ", 1)
            if t not in self.oublie:
                sortie.append(f"{n}. FR({t})")
        return "\n".join(sortie)


def test_traduire_textes_par_lots():
    t = FauxTraducteur()
    r = traduction.traduire_textes(t, [f"t{i}" for i in range(7)], taille_lot=3, journal=lambda m: None)
    assert r == [f"FR(t{i})" for i in range(7)]
    assert t.appels == 3


def test_ligne_manquante_reduit_le_lot_puis_garde_l_original():
    t = FauxTraducteur(oublie={"a"})   # cette ligne n'est jamais rendue par le modèle
    r = traduction.traduire_textes(t, ["a", "b", "c"], taille_lot=3, journal=lambda m: None)
    assert r[0] == "a"                 # gardée telle quelle en dernier recours
    assert r[1] == "FR(b)" and r[2] == "FR(c)"
