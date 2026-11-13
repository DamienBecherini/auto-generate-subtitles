"""Programme lancé par doublage.py dans l'environnement qui contient torch, demucs, omnivoice, soundfile et num2words.

Une vidéo entière : sépare la voix du fond sonore, regroupe les sous-titres traduits en phrases, génère chaque phrase en
clonant le passage d'origine correspondant (référence de 3 à 10 s, texte exact tiré des sous-titres d'origine), cale
chaque phrase sur son temps, puis mêle la voix au fond. Ne dépend pas du paquet soustitres.
Reçoit son travail en JSON sur la première ligne de l'entrée ; s'arrête si l'entrée se ferme.
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

for flux in (sys.stdin, sys.stdout, sys.stderr):
    flux.reconfigure(encoding="utf-8", errors="replace")

TAUX = 24000
TEMPS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
FIN_PHRASE = re.compile(r"[.?!…]\s*$")
ECART_PHRASE_S, PHRASE_MAX_S = 0.8, 12
REF_MIN_S, REF_MAX_S = 3, 10


def dire(cle, **params):
    """Avancement pour le journal : une clé de traduction et ses paramètres (voir doublage.message_travail)."""
    print("» " + json.dumps({"cle": cle, **params}, ensure_ascii=False), flush=True)


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *map(str, args)], check=True)


def lire_srt(chemin):
    blocs = []
    for bloc in re.split(r"\r?\n\r?\n", Path(chemin).read_text(encoding="utf-8-sig").strip()):
        lignes = bloc.splitlines()
        for i, l in enumerate(lignes):
            m = TEMPS.search(l)
            if m:
                v = list(map(int, m.groups()))
                blocs.append((v[0] * 3600 + v[1] * 60 + v[2] + v[3] / 1000, v[4] * 3600 + v[5] * 60 + v[6] + v[7] / 1000, " ".join(lignes[i + 1:]).strip()))
                break
    return blocs


def phrases(cues):
    """Regroupe les sous-titres en phrases entières, pour ne pas casser l'intonation à chaque coupure de ligne."""
    groupes, courant = [], []
    for c in cues:
        if courant and (c[0] - courant[-1][1] > ECART_PHRASE_S or c[1] - courant[0][0] > PHRASE_MAX_S):
            groupes.append(courant)
            courant = []
        courant.append(c)
        if FIN_PHRASE.search(c[2]):
            groupes.append(courant)
            courant = []
    if courant:
        groupes.append(courant)
    return [(g[0][0], g[-1][1], " ".join(x[2] for x in g)) for g in groupes]


def nombres_en_lettres(texte, langue):
    from num2words import num2words

    def conv(m):
        brut = m.group(0)
        n = int(re.sub(r"\D", "", brut))
        try:
            return num2words(n, lang=langue, to="year" if brut.isdigit() and 1100 <= n <= 2099 else "cardinal")
        except (NotImplementedError, ValueError, OverflowError):
            return brut
    return re.sub(r"\d(?:[\d   ]*\d)?", conv, texte)


def bornes_reference(cues, debut, fin):
    """Indices (i0, i1) des sous-titres d'origine qui servent de référence vocale à la phrase [debut, fin]."""
    indices = [i for i, c in enumerate(cues) if c[1] > debut and c[0] < fin]
    if not indices:
        indices = [min(range(len(cues)), key=lambda i: abs(cues[i][0] - debut))]
    i0, i1 = indices[0], indices[-1]
    while cues[i1][1] - cues[i0][0] < REF_MIN_S and (i1 + 1 < len(cues) or i0 > 0):
        if i1 + 1 < len(cues):
            i1 += 1
        else:
            i0 -= 1
    while cues[i1][1] - cues[i0][0] > REF_MAX_S and i1 > i0:
        i1 -= 1
    return i0, i1


def surveiller_entree():
    """Annulation ou arrêt du service : l'entrée se ferme, le programme sort sans attendre la phrase en cours."""
    sys.stdin.read()
    os._exit(130)


def principal():
    t = json.loads(sys.stdin.readline())
    threading.Thread(target=surveiller_entree, daemon=True).start()

    import numpy as np
    import soundfile as sf
    import torch
    from omnivoice import OmniVoice

    def rms(x):
        return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0

    tmp = Path(t["temporaire"])
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "refs").mkdir(exist_ok=True)
    debut_travail = time.time()
    duree = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", t["video"]],
                                 capture_output=True, text=True, check=True).stdout.strip())
    voix, fond = tmp / "demucs/htdemucs/extrait/vocals.wav", tmp / "demucs/htdemucs/extrait/no_vocals.wav"
    if not (voix.exists() and fond.exists()):              # déjà séparé : une reprise ne refait pas ces minutes
        extrait = tmp / "extrait.wav"
        ffmpeg("-i", t["video"], "-vn", "-ac", 2, "-ar", 44100, extrait)
        dire("log.dub_separating")
        subprocess.run([sys.executable, "-m", "demucs", "--two-stems", "vocals", "-o", str(tmp / "demucs"), str(extrait)], check=True)
        torch.cuda.empty_cache()
    v, taux_v = sf.read(voix)
    v = v.mean(axis=1)

    origine = lire_srt(t["srt_origine"])
    groupes = phrases(lire_srt(t["srt_cible"]))
    if not origine or not groupes:
        raise SystemExit("sous-titres vides")
    dire("log.dub_sentences", n=len(groupes))
    model = OmniVoice.from_pretrained(t["modele"], device_map="cuda:0", dtype=torch.float16)
    piste = np.zeros(int(duree * TAUX) + TAUX)
    for i, (a, b, texte) in enumerate(groupes):
        i0, i1 = bornes_reference(origine, a, b)
        ra, rb = origine[i0][0], origine[i1][1]
        morceau = v[int(ra * taux_v):int(rb * taux_v)]
        ref = tmp / "refs" / "phrase.wav"
        sf.write(ref, morceau, taux_v)
        audio = model.generate(text=nombres_en_lettres(texte, t["langue"]), ref_audio=str(ref), ref_text=" ".join(c[2] for c in origine[i0:i1 + 1]),
                               duration=b - a, num_step=t["pas"], language=t["langue"])[0]
        gain = min(3.0, max(0.3, rms(morceau) / max(rms(audio), 1e-6)))
        audio = np.clip(audio * gain, -1, 1)
        debut = int(a * TAUX)
        piste[debut:debut + len(audio)] += audio[:len(piste) - debut]
        if (i + 1) % 25 == 0 or i + 1 == len(groupes):
            dire("log.dub_progress", done=i + 1, n=len(groupes), s=round(time.time() - debut_travail))

    pv = tmp / "voix.wav"
    sf.write(pv, piste, TAUX)
    partiel = tmp / "mix.m4a"
    ffmpeg("-i", pv, "-i", fond, "-filter_complex", "[0:a]aresample=44100,aformat=channel_layouts=stereo[v];[v][1:a]amix=inputs=2:normalize=0,alimiter=limit=0.95[m]",
           "-map", "[m]", "-t", duree, "-c:a", "aac", "-b:a", "192k", partiel)
    os.replace(partiel, t["sortie"])
    shutil.rmtree(tmp, ignore_errors=True)
    dire("log.dub_done", s=round(time.time() - debut_travail))


if __name__ == "__main__":
    principal()
