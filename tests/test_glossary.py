"""Validated glossary: exact fix of the words, timings kept, translation instructions."""
import json

from subtitles import glossary, translation

E = [{"transcribed": "Tajevsky", "spelling": "Chizhevsky", "french": "Tchijevski"},
     {"transcribed": "Corey Good", "spelling": "Corey Goode", "french": ""},
     {"transcribed": "Padma Sambhava", "spelling": "Padmasambhava", "french": ""},
     {"transcribed": "Plato", "spelling": "Plato", "french": "Platon"}]


def w(*words):
    return [{"word": x, "start": float(i), "end": i + 0.5} for i, x in enumerate(words)]


def texts(words):
    return [x["word"] for x in words]


def test_fixes_a_word_and_keeps_punctuation_and_timings():
    r, n = glossary.fix_words(w("scientist", "Tajevsky,", "was"), E)
    assert texts(r) == ["scientist", "Chizhevsky,", "was"] and n == 1
    assert (r[1]["start"], r[1]["end"]) == (1.0, 1.5)


def test_keeps_the_possessive():
    assert texts(glossary.fix_words(w("Tajevsky's", "work"), E)[0]) == ["Chizhevsky's", "work"]


def test_several_words_same_count():
    r, _ = glossary.fix_words(w("with", "Corey", "Good."), E)
    assert texts(r) == ["with", "Corey", "Goode."]


def test_several_merged_words_cover_the_whole_time():
    r, n = glossary.fix_words(w("by", "Padma", "Sambhava!"), E)
    assert texts(r) == ["by", "Padmasambhava!"] and n == 1
    assert (r[1]["start"], r[1]["end"]) == (1.0, 2.5)


def test_exact_word_only():
    assert glossary.fix_words(w("Tajevskyan", "tajevsky", "Good"), E) == (w("Tajevskyan", "tajevsky", "Good"), 0)


def test_original_words_not_modified():
    d = w("Tajevsky")
    glossary.fix_words(d, E)
    assert d[0]["word"] == "Tajevsky"


def test_without_glossary_nothing_changes(tmp_path):
    assert glossary.load(tmp_path / "absent.json") == [] and glossary.load(None) == []
    (tmp_path / "x.json").write_text("{broken", encoding="utf-8")
    assert glossary.load(tmp_path / "x.json") == []
    d = w("Tajevsky")
    assert glossary.fix_words(d, []) == (d, 0)


def test_load_reads_the_entries(tmp_path):
    f = tmp_path / glossary.FILE_NAME
    f.write_text(json.dumps(E), encoding="utf-8")
    assert len(glossary.load(f)) == 4


def test_terms_only_those_of_the_video():
    t = glossary.terms_for(E, ["We read Chizhevsky and Plato today"], "fr")
    assert t == ["Chizhevsky → Tchijevski", "Plato → Platon"]


def test_terms_other_language_without_french_form():
    assert glossary.terms_for(E, ["Chizhevsky"], "de") == ["Chizhevsky"]


def test_instructions_hold_the_names():
    c = translation.instructions("fr", ["Chizhevsky → Tchijevski"])
    assert "Chizhevsky → Tchijevski" in c
    assert "Noms à respecter" not in translation.instructions("fr")


def test_replace_text_whole_word_longest_first():
    t, n = glossary.replace_text("Corey Good et Goody, Tajevsky.", [("Good", "X"), ("Corey Good", "Corey Goode"), ("Tajevsky", "Tchijevski")])
    assert t == "Corey Goode et Goody, Tchijevski." and n == 2
