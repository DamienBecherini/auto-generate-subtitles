"""Makes demo videos: narration (Windows speech synthesis: Hortense in French, Zira in English) over an animated
gradient background with a title. No outside source, so no copyright involved.
The French videos are used for the screenshots of the French README, the English ones for the English README.

    python videos_demo.py <output folder> [fr|en]
"""
import subprocess
import sys
import tempfile
from pathlib import Path

VIDEOS_FR = [
    ("01 - La règle 3-2-1", "La règle 3-2-1",
     "Bonjour, et bienvenue dans ce court épisode consacré aux sauvegardes. La règle trois, deux, un est simple à "
     "retenir. Gardez trois exemplaires de vos données, sur deux supports différents, dont un hors de chez vous. "
     "Un disque peut tomber en panne, un appartement peut brûler, un rançongiciel peut tout chiffrer. Avec trois "
     "copies bien réparties, aucun de ces accidents ne vous fera perdre vos photos ou vos documents. Et surtout, "
     "testez une restauration de temps en temps : une sauvegarde qu'on n'a jamais relue n'est qu'un espoir."),
    ("02 - Pourquoi auto-héberger", "Pourquoi auto-héberger ?",
     "Auto-héberger ses services, c'est reprendre la main sur ses données. Vos fichiers, votre agenda et vos "
     "photos restent chez vous, sur une machine que vous connaissez. Il faut en échange un peu de discipline : "
     "des mises à jour régulières, des sauvegardes vérifiées, et un œil sur l'état des machines. C'est "
     "exactement à cela que servent les pastilles de supervision : un coup d'œil suffit pour savoir si tout va bien."),
    ("03 - Les pastilles", "Vert, orange, rouge, gris",
     "Une pastille verte signifie que tout va bien. Orange, il faudra regarder bientôt : un disque qui se "
     "remplit, un certificat qui expire. Rouge, il faut agir maintenant. Et gris, c'est peut-être le plus "
     "important : le contrôle ne répond plus. Le silence n'est pas un succès."),
]

VIDEOS_EN = [
    ("01 - The 3-2-1 rule", "The 3-2-1 rule",
     "Hello, and welcome to this short episode about backups. The three, two, one rule is easy to remember. Keep "
     "three copies of your data, on two different media, with one of them away from home. A disk can fail, a flat "
     "can burn, ransomware can encrypt everything. With three well-spread copies, none of these accidents will make "
     "you lose your photos or your documents. And above all, test a restore from time to time: a backup you have "
     "never read back is only a hope."),
    ("02 - Why self-host", "Why self-host?",
     "Self-hosting your services means taking back control of your data. Your files, your calendar and your photos "
     "stay at home, on a machine you know. In exchange, it takes a little discipline: regular updates, verified "
     "backups, and an eye on the state of your machines. That is exactly what the status dots are for: one glance "
     "is enough to know whether everything is fine."),
    ("03 - The status dots", "Green, orange, red, grey",
     "A green dot means everything is fine. Orange, you will need to look soon: a disk filling up, a certificate "
     "about to expire. Red, you must act now. And grey is perhaps the most important: the check no longer "
     "answers. Silence is not success."),
]
VIDEOS = {"fr": VIDEOS_FR, "en": VIDEOS_EN}
VOICES = {"fr": "Microsoft Hortense Desktop", "en": "Microsoft Zira Desktop"}

FONT = "C\\:/Windows/Fonts/segoeuib.ttf"


def speak(text, wav, voice):
    ps = (f"Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.SelectVoice('{voice}'); $s.Rate = 0; "
          f"$s.SetOutputToWaveFile('{wav}'); $s.Speak([IO.File]::ReadAllText('{wav}.txt')); $s.Dispose()")
    Path(f"{wav}.txt").write_text(text, encoding="utf-8-sig")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


def main():
    output = Path(sys.argv[1])
    language = sys.argv[2] if len(sys.argv) > 2 else "fr"
    output.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="videos-demo-"))
    for i, (name, title, text) in enumerate(VIDEOS[language]):
        wav = work / f"{i}.wav"
        speak(text, str(wav), VOICES[language])
        colours = ["0x1d4e89 0x2a9d8f", "0x6a4c93 0x1982c4", "0x264653 0xe76f51"][i]
        c1, c2 = colours.split()
        ff_title = title.replace("'", "’").replace(":", "\\:")
        vf = (f"drawtext=fontfile='{FONT}':text='{ff_title}':fontcolor=white:fontsize=64:"
                  f"x=(w-text_w)/2:y=h*0.32:shadowcolor=black@0.4:shadowx=3:shadowy=3")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                        "-f", "lavfi", "-i", f"gradients=s=1280x720:c0={c1}:c1={c2}:speed=0.008:r=25",
                        "-i", str(wav), "-vf", vf, "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "128k", str(output / f"{name}.mp4")], check=True)
        print(name)


if __name__ == "__main__":
    main()
