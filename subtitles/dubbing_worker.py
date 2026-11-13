"""Program run by dubbing.py in the environment that holds torch, demucs, omnivoice, soundfile and num2words.

A whole video: separates the voice from the background sound, groups the translated subtitles into sentences,
generates each sentence by cloning the matching original passage (reference of 3 to 10 s, exact text taken from the
original subtitles), fits each sentence to its time, then mixes the voice with the background. Does not depend on the
subtitles package. Receives its job as JSON on the first line of its input; stops if the input closes.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

for stream in (sys.stdin, sys.stdout, sys.stderr):
    stream.reconfigure(encoding="utf-8", errors="replace")

RATE = 24000
TIMES = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
END_OF_SENTENCE = re.compile(r"[.?!…]\s*$")
SENTENCE_GAP_S, SENTENCE_MAX_S = 0.8, 12
REF_MIN_S, REF_MAX_S = 3, 10


def say(key, **params):
    """Progress for the log: a translation key and its parameters (see dubbing.worker_message)."""
    print("» " + json.dumps({"key": key, **params}, ensure_ascii=False), flush=True)


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *map(str, args)], check=True)


def read_srt(path):
    blocks = []
    for block in re.split(r"\r?\n\r?\n", Path(path).read_text(encoding="utf-8-sig").strip()):
        lines = block.splitlines()
        for i, line in enumerate(lines):
            m = TIMES.search(line)
            if m:
                v = list(map(int, m.groups()))
                blocks.append((v[0] * 3600 + v[1] * 60 + v[2] + v[3] / 1000, v[4] * 3600 + v[5] * 60 + v[6] + v[7] / 1000, " ".join(lines[i + 1:]).strip()))
                break
    return blocks


def sentences(cues):
    """Groups the subtitles into whole sentences, so as not to break the intonation at each line break."""
    groups, current = [], []
    for c in cues:
        if current and (c[0] - current[-1][1] > SENTENCE_GAP_S or c[1] - current[0][0] > SENTENCE_MAX_S):
            groups.append(current)
            current = []
        current.append(c)
        if END_OF_SENTENCE.search(c[2]):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return [(g[0][0], g[-1][1], " ".join(x[2] for x in g)) for g in groups]


def numbers_to_words(text, language):
    from num2words import num2words

    def conv(m):
        raw = m.group(0)
        n = int(re.sub(r"\D", "", raw))
        try:
            return num2words(n, lang=language, to="year" if raw.isdigit() and 1100 <= n <= 2099 else "cardinal")
        except (NotImplementedError, ValueError, OverflowError):
            return raw
    return re.sub(r"\d(?:[\d   ]*\d)?", conv, text)


def reference_bounds(cues, start, end):
    """Indices (i0, i1) of the original subtitles used as the voice reference of the sentence [start, end]."""
    indices = [i for i, c in enumerate(cues) if c[1] > start and c[0] < end]
    if not indices:
        indices = [min(range(len(cues)), key=lambda i: abs(cues[i][0] - start))]
    i0, i1 = indices[0], indices[-1]
    while cues[i1][1] - cues[i0][0] < REF_MIN_S and (i1 + 1 < len(cues) or i0 > 0):
        if i1 + 1 < len(cues):
            i1 += 1
        else:
            i0 -= 1
    while cues[i1][1] - cues[i0][0] > REF_MAX_S and i1 > i0:
        i1 -= 1
    return i0, i1


def watch_input():
    """Cancellation or service shutdown: the input closes, the program exits without waiting for the current sentence."""
    sys.stdin.read()
    os._exit(130)


def main():
    t = json.loads(sys.stdin.readline())
    threading.Thread(target=watch_input, daemon=True).start()

    import numpy as np
    import soundfile as sf
    import torch
    from omnivoice import OmniVoice

    def rms(x):
        return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0

    tmp = Path(t["temp_dir"])
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "refs").mkdir(exist_ok=True)
    job_start = time.time()
    duration = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", t["video"]],
                                    capture_output=True, text=True, check=True).stdout.strip())
    voice, background = tmp / "demucs/htdemucs/extract/vocals.wav", tmp / "demucs/htdemucs/extract/no_vocals.wav"
    if not (voice.exists() and background.exists()):     # already separated: a resume does not redo these minutes
        extract = tmp / "extract.wav"
        ffmpeg("-i", t["video"], "-vn", "-ac", 2, "-ar", 44100, extract)
        say("log.dub_separating")
        subprocess.run([sys.executable, "-m", "demucs", "--two-stems", "vocals", "-o", str(tmp / "demucs"), str(extract)], check=True)
        torch.cuda.empty_cache()
    v, rate_v = sf.read(voice)
    v = v.mean(axis=1)

    original = read_srt(t["source_srt"])
    groups = sentences(read_srt(t["target_srt"]))
    if not original or not groups:
        raise SystemExit("empty subtitles")
    say("log.dub_sentences", n=len(groups))
    model = OmniVoice.from_pretrained(t["model"], device_map="cuda:0", dtype=torch.float16)
    track = np.zeros(int(duration * RATE) + RATE)
    for i, (a, b, text) in enumerate(groups):
        i0, i1 = reference_bounds(original, a, b)
        ra, rb = original[i0][0], original[i1][1]
        piece = v[int(ra * rate_v):int(rb * rate_v)]
        ref = tmp / "refs" / "sentence.wav"
        sf.write(ref, piece, rate_v)
        audio = model.generate(text=numbers_to_words(text, t["language"]), ref_audio=str(ref), ref_text=" ".join(c[2] for c in original[i0:i1 + 1]),
                               duration=b - a, num_step=t["steps"], language=t["language"])[0]
        gain = min(3.0, max(0.3, rms(piece) / max(rms(audio), 1e-6)))
        audio = np.clip(audio * gain, -1, 1)
        start = int(a * RATE)
        track[start:start + len(audio)] += audio[:len(track) - start]
        if (i + 1) % 25 == 0 or i + 1 == len(groups):
            say("log.dub_progress", done=i + 1, n=len(groups), s=round(time.time() - job_start))

    pv = tmp / "voice.wav"
    sf.write(pv, track, RATE)
    partial = tmp / "mix.m4a"
    ffmpeg("-i", pv, "-i", background, "-filter_complex", "[0:a]aresample=44100,aformat=channel_layouts=stereo[v];[v][1:a]amix=inputs=2:normalize=0,alimiter=limit=0.95[m]",
           "-map", "[m]", "-t", duration, "-c:a", "aac", "-b:a", "192k", partial)
    os.replace(partial, t["output"])
    shutil.rmtree(tmp, ignore_errors=True)
    say("log.dub_done", s=round(time.time() - job_start))


if __name__ == "__main__":
    main()
