# auto-generate-subtitles

Subtitles for a YouTube video or a video file, in its own language and in the languages you want. You bring the transcription server; translation runs on a local LLM, the Claude API, any OpenAI-compatible API, or not at all. Output is `.srt` and `.vtt` files timed on the actual speech.

[Version française](README.fr.md)

## What it does

- **One video or a whole folder.** A folder becomes a batch you can pause, resume after a power cut, and retry video by video.
- **Cost before spending.** With a paid translator, the batch stops after transcription and shows the estimated tokens and price for each model.
- **No retyped work.** Each step is skipped when its result already exists: changing a language or the layout does not transcribe again.
- **Web page or command line.** The page shows steps, live log and remaining time. It runs on your machine and listens on `127.0.0.1` only.

## Quick start

**1. Start a transcription server.** This tool sends the audio to a server you run. Use **[transcription-handy-like](https://github.com/DamienBecherini/transcription-handy-like)**: its [quick start](https://github.com/DamienBecherini/transcription-handy-like#quick-start-on-your-computer) takes three commands with Docker, and it listens on the address expected here by default.

**2. Install this tool.** You need Python 3.11 or later, and `ffmpeg` and `ffprobe` in the `PATH`. `yt-dlp` is needed for YouTube addresses only.

```
git clone https://github.com/DamienBecherini/auto-generate-subtitles.git
cd auto-generate-subtitles
python -m venv .venv
.venv/bin/pip install .             # Windows: .venv/Scripts/python -m pip install .
.venv/bin/python -m soustitres.web --ouvrir
```

**3. Check the connection.** On the page, open **Réglages**, then click **Tester** next to the transcription server. If your server is on another machine or uses an API key, change its address and key there.

**4. Make subtitles.** Paste a YouTube address or a file path, then click **Démarrer**. The `.srt` file appears next to the video.

To translate, add a translator in **Réglages**. For the Claude API, first run `.venv/bin/pip install ".[claude]"`.

## Transcription server requirements

Any server can be used if it meets this contract. The tool needs an OpenAI-compatible server that returns **word timestamps**: `POST /v1/audio/transcriptions` with `response_format=verbose_json` and `timestamp_granularities[]=word`, answering `words: [{"word", "start", "end"}]` on the recording's timeline. A server that only returns segments is refused with a clear message. A `/health` route is optional.

Tested with [transcription-handy-like](https://github.com/DamienBecherini/transcription-handy-like) (recommended) and the Docker image `hwdsl2/whisper-server`.

## Use

```
.venv/bin/python -m soustitres.web --ouvrir        # web page on http://127.0.0.1:4420/
.venv/bin/python -m soustitres "https://www.youtube.com/watch?v=..."
.venv/bin/python -m soustitres "my video.mp4" --langues fr,de
.venv/bin/python -m soustitres --liste              # configured engines
```

Command line options: `--sortie`, `--langue en` (spoken language), `--langues fr,de` (target languages), `--transcription <id>`, `--traducteur <id>`, `--refaire`, `--sans-nettoyage`.

The interface and options are in French for now.

File pickers use the system dialog: PowerShell on Windows, `osascript` on macOS, `zenity` or `kdialog` on Linux. Without any of them, type the path in the field.

## Settings

Open **Réglages** on the page. Your settings are saved in `config.json` in the user folder (`%LOCALAPPDATA%\sous-titres` on Windows, `$XDG_CONFIG_HOME/sous-titres` or `~/.config/sous-titres` elsewhere). They override the defaults shipped in `soustitres/config.toml`, which only contains examples.

**API keys are never stored in clear text.** The key field holds a reference, and a plain value is refused:

| Reference | Meaning |
| --- | --- |
| `env:<NAME>` | environment variable |
| `file:<path>` | file containing the key, outside the repository |
| `sops:<file>#<NAME>` | decrypted when used, if sops is installed |

## How it works

1. **Source.** `yt-dlp` downloads the video into the output folder if needed, then `ffmpeg` extracts the audio.
2. **Transcription.** The audio goes to your server; the result is kept in `transcription.json`.
3. **Cutting.** Words are grouped into sentences, then into subtitles of at most 2 lines of 42 characters and 6 seconds.
4. **Translation.** Numbered batches of 25 lines, with context. Timings do not change.
5. **Files.** Each `.srt` is copied next to the video, where most players load it automatically, and/or into the output folder.
6. **Cleanup.** Extracted audio is removed; `transcription.json` and translations are kept.

## Local service

`GET /api/sante` reports whether the service is busy, its current activity (`lot 3/22`), the time left and the idle countdown. After 60 minutes without activity the service stops by itself; running work is never interrupted.

## Development

```
.venv/bin/pip install -e ".[dev,claude]"
.venv/bin/pytest -q
.venv/bin/ruff check .
```

## License

MIT, see [LICENSE](LICENSE).
