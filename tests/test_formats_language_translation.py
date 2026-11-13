from subtitles import formats, language, translation
from subtitles.segmentation import Cue, Settings


def test_hms():
    assert formats.hms(0) == "00:00:00,000"
    assert formats.hms(3661.5) == "01:01:01,500"
    assert formats.hms(59.9996, ".") == "00:01:00.000"


def test_srt_and_vtt():
    cues = [Cue(0.0, 1.5, "Bonjour à tous"), Cue(2.0, 4.0, "Deuxième sous-titre")]
    s = formats.srt(cues, Settings())
    assert s.startswith("1\n00:00:00,000 --> 00:00:01,500\nBonjour à tous\n\n2\n00:00:02,000 --> 00:00:04,000\n")
    v = formats.vtt(cues, Settings())
    assert v.startswith("WEBVTT\n\n00:00:00.000 --> 00:00:01.500\nBonjour à tous\n")


def test_language_detection():
    assert language.detect("Hello everyone and welcome back to the channel, today we are going to talk about it") == "en"
    assert language.detect("Bonjour à tous, aujourd'hui nous allons parler des sous-titres et de la traduction") == "fr"
    assert language.detect("xyz qrs") == ""


def test_parse_answer():
    r = translation.parse_answer("1. Bonjour à tous\n2) Bienvenue\nsur la chaîne\n3 - Troisième", 3)
    assert r == {1: "Bonjour à tous", 2: "Bienvenue sur la chaîne", 3: "Troisième"}
    assert translation.parse_answer("Voici :\n1. A\n2. ", 2) == {1: "A"}


class FakeTranslator:
    def __init__(self, forgets=()):
        self.forgets = set(forgets)
        self.calls = 0

    def prepare(self):
        pass

    def finish(self):
        pass

    def complete(self, system, request):
        self.calls += 1
        block = request.split("Lignes à traduire :\n", 1)[1].split("\n\n", 1)[0]
        out = []
        for line in block.splitlines():
            n, t = line.split(". ", 1)
            if t not in self.forgets:
                out.append(f"{n}. FR({t})")
        return "\n".join(out)


def test_translate_texts_in_chunks():
    t = FakeTranslator()
    r = translation.translate_texts(t, [f"t{i}" for i in range(7)], chunk_size=3, log=lambda m: None)
    assert r == [f"FR(t{i})" for i in range(7)]
    assert t.calls == 3


def test_missing_line_shrinks_the_chunk_then_keeps_the_original():
    t = FakeTranslator(forgets={"a"})   # this line is never given back by the model
    r = translation.translate_texts(t, ["a", "b", "c"], chunk_size=3, log=lambda m: None)
    assert r[0] == "a"                  # kept as it is as a last resort
    assert r[1] == "FR(b)" and r[2] == "FR(c)"
