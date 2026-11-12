"""Fabrique des vidéos de démonstration : narration française (synthèse vocale Windows, voix Hortense) sur un
fond en dégradé animé avec un titre. Aucune source extérieure, donc aucun droit d'auteur en jeu.

    python videos_demo.py <dossier de sortie>
"""
import subprocess
import sys
import tempfile
from pathlib import Path

VIDEOS = [
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

POLICE = "C\\:/Windows/Fonts/segoeuib.ttf"


def parler(texte, wav):
    ps = (f"Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.SelectVoice('Microsoft Hortense Desktop'); $s.Rate = 0; "
          f"$s.SetOutputToWaveFile('{wav}'); $s.Speak([IO.File]::ReadAllText('{wav}.txt')); $s.Dispose()")
    Path(f"{wav}.txt").write_text(texte, encoding="utf-8-sig")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


def main():
    sortie = Path(sys.argv[1])
    sortie.mkdir(parents=True, exist_ok=True)
    travail = Path(tempfile.mkdtemp(prefix="videos-demo-"))
    for i, (nom, titre, texte) in enumerate(VIDEOS):
        wav = travail / f"{i}.wav"
        parler(texte, str(wav))
        teintes = ["0x1d4e89 0x2a9d8f", "0x6a4c93 0x1982c4", "0x264653 0xe76f51"][i]
        c1, c2 = teintes.split()
        titre_ff = titre.replace("'", "’").replace(":", "\\:")
        filtre = (f"drawtext=fontfile='{POLICE}':text='{titre_ff}':fontcolor=white:fontsize=64:"
                  f"x=(w-text_w)/2:y=h*0.32:shadowcolor=black@0.4:shadowx=3:shadowy=3")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                        "-f", "lavfi", "-i", f"gradients=s=1280x720:c0={c1}:c1={c2}:speed=0.008:r=25",
                        "-i", str(wav), "-vf", filtre, "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "128k", str(sortie / f"{nom}.mp4")], check=True)
        print(nom)


if __name__ == "__main__":
    main()
