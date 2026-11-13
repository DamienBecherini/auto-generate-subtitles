from subtitles import segmentation
from subtitles.segmentation import Cue, Settings, Word


def words(sentence: str, start=0.0, step=0.3):
    return [Word(w, round(start + i * step, 3), round(start + i * step + step * 0.8, 3)) for i, w in enumerate(sentence.split())]


def test_sentences_split_on_punctuation_and_pause():
    w = words("Bonjour à tous. Aujourd'hui on parle") + words("de sous-titres automatiques", start=10.0)
    p = segmentation.sentences(w, Settings())
    assert [" ".join(x.text for x in s) for s in p] == ["Bonjour à tous.", "Aujourd'hui on parle", "de sous-titres automatiques"]


def test_long_sentence_split_into_readable_blocks():
    text = ("Alors aujourd'hui nous allons voir comment générer des sous-titres automatiquement, "
            "en transcrivant l'audio puis en traduisant chaque ligne sans perdre la synchronisation")
    r = Settings()
    cues = segmentation.build(words(text), r)
    assert len(cues) >= 2
    for s in cues:
        assert len(s.text) <= r.max_chars
        assert s.end - s.start <= r.max_duration + 2.5   # reading time added at most
    assert " ".join(s.text for s in cues) == text
    # split after the comma
    assert any(s.text.endswith("automatiquement,") for s in cues)


def test_no_overlap_and_minimum_duration():
    w = words("Oui. Non. Peut-être bien que oui finalement.", step=0.25)
    cues = segmentation.build(w, Settings(min_duration=1.0, min_gap=0.08))
    for a, b in zip(cues, cues[1:]):
        assert b.start >= a.end + 0.08 - 1e-9
    assert all(s.end > s.start for s in cues)
    assert cues[-1].end - cues[-1].start >= 1.0


def test_wrap_lines_balance_and_punctuation():
    r = Settings(chars_per_line=42)
    lines = segmentation.wrap_lines("Ceci est une première phrase assez longue, et voici la suite du propos", r)
    assert len(lines) == 2 and all(len(x) <= 42 for x in lines)
    assert lines[0].endswith(",")
    assert segmentation.wrap_lines("Court", r) == ["Court"]


def test_split_too_long_shares_the_time():
    long_text = "mot " * 40
    cues = segmentation.split_too_long([Cue(0.0, 8.0, long_text.strip())], Settings())
    assert len(cues) >= 2
    assert cues[0].start == 0.0 and cues[-1].end == 8.0
    assert all(len(s.text) <= Settings().max_chars for s in cues)
    for a, b in zip(cues, cues[1:]):
        assert b.start >= a.end


def test_from_json_ignores_empty_words():
    w = segmentation.from_json([{"word": " Hello", "start": 0, "end": 0.5}, {"word": "  ", "start": 0.5, "end": 0.6}])
    assert [x.text for x in w] == ["Hello"]
