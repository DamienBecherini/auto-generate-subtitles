"""From timestamped words to subtitles: sentences, split into readable blocks, display durations, line breaks.

Usual subtitle rules: at most 2 lines of 42 characters, 1 to 6 s on screen, split preferably after punctuation or
in a pause, balanced lines, no overlap.
"""
import re
from dataclasses import dataclass, field

END_OF_SENTENCE = re.compile(r"[.!?…]+[\"»”’)\]]*$")
WEAK_PUNCTUATION = re.compile(r"[,;:—–-]+[\"»”’)\]]*$")


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Cue:
    start: float
    end: float
    text: str                      # a single line (line breaks are added when writing)
    words: list[Word] = field(default_factory=list)


@dataclass
class Settings:
    chars_per_line: int = 42
    lines: int = 2
    max_duration: float = 6.0
    min_duration: float = 1.0
    split_pause: float = 0.6
    min_gap: float = 0.08
    reading_cps: float = 17

    @property
    def max_chars(self) -> int:
        return self.chars_per_line * self.lines


def from_json(words: list[dict]) -> list[Word]:
    result = []
    for w in words:
        t = str(w.get("word", "")).strip()
        if t:
            result.append(Word(t, float(w["start"]), float(w["end"])))
    return result


def sentences(words: list[Word], r: Settings) -> list[list[Word]]:
    """Splits after the end of a sentence or before a pause of `split_pause` seconds or more."""
    result, current = [], []
    for i, w in enumerate(words):
        current.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None or END_OF_SENTENCE.search(w.text) or nxt.start - w.end >= r.split_pause:
            result.append(current)
            current = []
    return result


def _length(words: list[Word]) -> int:
    return sum(len(w.text) for w in words) + max(0, len(words) - 1)


def _fits(words: list[Word], r: Settings) -> bool:
    return _length(words) <= r.max_chars and words[-1].end - words[0].start <= r.max_duration


def _split_score(words: list[Word], i: int) -> float:
    """Split between words[i] and words[i+1]: long pause, punctuation, balance of the two parts."""
    left, right = words[: i + 1], words[i + 1:]
    s = 2.0 * min(right[0].start - left[-1].end, 1.0)
    if END_OF_SENTENCE.search(left[-1].text):
        s += 1.5
    elif WEAK_PUNCTUATION.search(left[-1].text):
        s += 1.0
    total = _length(words) or 1
    s -= 1.5 * abs(_length(left) / total - 0.5)
    if len(left) == 1 or len(right) == 1:
        s -= 1.0            # no orphan word
    return s


def split(words: list[Word], r: Settings) -> list[list[Word]]:
    """Recursive split of a sentence that is too long (in characters or in seconds) at the best place."""
    if len(words) <= 1 or _fits(words, r):
        return [words]
    i = max(range(len(words) - 1), key=lambda k: _split_score(words, k))
    return split(words[: i + 1], r) + split(words[i + 1:], r)


def adjust_durations(cues: list[Cue], r: Settings) -> list[Cue]:
    """Each subtitle stays long enough to be read, without overlapping the next one."""
    for i, s in enumerate(cues):
        if i and s.start < cues[i - 1].end + r.min_gap:
            s.start = round(cues[i - 1].end + r.min_gap, 3)      # very fast speech: the next one is pushed back
        wanted = max(r.min_duration, len(s.text) / r.reading_cps)
        limit = cues[i + 1].start - r.min_gap if i + 1 < len(cues) else float("inf")
        end = min(max(s.end, s.start + wanted), limit)
        s.end = round(max(end, s.start + 0.3), 3)                 # 0.3 s at least, even if the next one is pushed back
    return cues


def build(words: list[Word], r: Settings | None = None) -> list[Cue]:
    r = r or Settings()
    cues = []
    for p in sentences(words, r):
        for block in split(p, r):
            cues.append(Cue(round(block[0].start, 3), round(block[-1].end, 3), " ".join(w.text for w in block), block))
    return adjust_durations(cues, r)


def wrap_lines(text: str, r: Settings) -> list[str]:
    """One or two balanced lines, split after punctuation when possible."""
    text = " ".join(text.split())
    if len(text) <= r.chars_per_line or r.lines < 2 or " " not in text:
        return [text]
    return list(split_in_two(text, r.chars_per_line))


def split_in_two(text: str, width: int) -> tuple[str, str]:
    """Best split at a space: two parts of at most `width` if possible, balanced, after punctuation."""
    best, best_score = None, float("-inf")
    for m in re.finditer(r" ", text):
        left, right = text[: m.start()], text[m.end():]
        if len(left) > width or len(right) > width:
            penalty = -5.0 - (max(len(left), len(right)) - width)
        else:
            penalty = 0.0
        s = penalty - abs(len(left) - len(right)) / 10
        if END_OF_SENTENCE.search(left):
            s += 2.0
        elif WEAK_PUNCTUATION.search(left):
            s += 1.5
        if len(right.split()) == 1 and len(right) < 6:
            s -= 1.0        # not a short word alone on the second line
        if s > best_score:
            best, best_score = (left, right), s
    return best


def split_too_long(cues: list[Cue], r: Settings) -> list[Cue]:
    """After translation, a text that no longer fits on two lines is shared between two subtitles, the time
    split in proportion to the characters (the original words no longer match)."""
    result = []
    for s in cues:
        text = " ".join(s.text.split())
        if len(text) <= r.max_chars or " " not in text:
            result.append(Cue(s.start, s.end, text))
            continue
        g, d = split_in_two(text, r.max_chars)
        part = len(g) / max(len(text), 1)
        middle = round(s.start + (s.end - s.start) * part, 3)
        result.extend(split_too_long([Cue(s.start, max(middle - r.min_gap / 2, s.start + 0.3), g),
                                      Cue(min(middle + r.min_gap / 2, s.end - 0.3), s.end, d)], r))
    return result
