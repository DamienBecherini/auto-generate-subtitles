# Installing dl-sub-dub with an AI coding agent

This file is for an AI coding agent (Claude Code, Codex, Cursor…) asked to install and configure
dl-sub-dub on the user's machine. The human-facing documentation is [README.md](README.md). Follow the
steps in order, and keep the user in the loop: they choose the engines and the languages, you find what the
machine already has.

## Ground rules

- **Ask before anything that needs a password, administrator rights, a new account, a paid service or a change to
  another program** (Docker, a package manager, a firewall). Show the exact command and let the user run it if
  they prefer.
- **Never write a secret in a file you create, nor in the conversation.** An API key in the settings is a
  reference: `env:NAME`, `file:/path/to/file` or `sops:/path/to/file.env#NAME`; a plain value is refused. When a
  translator needs a key, tell the user where to create it and where to store it, then write only the reference.
- **Discovery is read-only.** Look for programs and listening ports; do not change them, do not scan the network.
- **Show the settings before writing them**, and explain each choice in one line.
- **Only process videos the user has the right to use**, and only clone a voice (dubbing) with that right too.
- **The interface texts are in English and French**; the code, the API, the JSON fields and the setting keys are
  in English. Explain them in the user's language.

## 1. Find out where you are

- Operating system: Windows, Linux or macOS. Python 3.11 or later is required: `python` on Windows, `python3` on
  Linux and macOS (Debian and Ubuntu may lack the `python3-venv` package: installing it needs administrator
  rights).
- `ffmpeg` and `ffprobe` must be in the `PATH` (`ffmpeg -version`). If they are missing, propose the usual way and
  ask first: `winget install Gyan.FFmpeg` (Windows), `brew install ffmpeg` (macOS), `sudo apt install ffmpeg`
  (Debian, Ubuntu).
- `yt-dlp` is needed only for video and playlist addresses (tested: YouTube, Dailymotion, PeerTube, Arte; other sites it supports may work, not those that require an account). Ask whether the
  user wants them; if so, propose `winget install yt-dlp.yt-dlp`, `brew install yt-dlp` or `pipx install yt-dlp`.
  For YouTube, recent versions also need a JavaScript runtime (Deno, or Node 22 or later given with
  `--js-runtimes node:<path>` in the yt-dlp configuration file): check with `yt-dlp --simulate <a short public video>`.
- The port: 4420. If something already answers on `http://127.0.0.1:4420/`, ask what it is before going on.
- Install folder: ask, and default to `~/dl-sub-dub`.
- **The power of the machine**, read-only, for the recommendations of steps 3 and 4: processor and number of
  cores, RAM, graphics card and its video memory (`nvidia-smi --query-gpu=name,memory.total --format=csv`;
  Windows: `Get-CimInstance Win32_VideoController`; macOS: `system_profiler SPHardwareDataType SPDisplaysDataType`,
  where Apple Silicon memory is shared by the processor and the graphics), free disk space (a local model takes
  5 to 25 GB). Also note a local model server already installed or running (llama.cpp, Ollama, LM Studio).

## 2. Install

```
git clone https://github.com/DamienBecherini/dl-sub-dub.git <install folder>
cd <install folder>
python3 -m venv .venv                         # Windows: python -m venv .venv
.venv/bin/pip install .                       # Windows: .venv/Scripts/python -m pip install .
```

Add `".[claude]"` instead of `.` if the user chooses the Claude API in step 4. `pip install .` copies the code into
the virtual environment: after a `git pull`, run it again.

## 3. A transcription server

The tool sends the audio to a server that returns **word timestamps** (contract in the README, "Transcription
server requirements"). Check, in this order:

1. Something answers on `http://127.0.0.1:8000/health`: probably a server already installed. Ask the user.
2. The user has one on another machine (home server, NAS): ask for its address, and whether it needs an API key
   (then a reference, see the ground rules).
3. Otherwise, propose **[transcription-handy-like](https://github.com/DamienBecherini/transcription-handy-like)**
   with Docker: three commands in its quick start, about 1 GB of models downloaded on first start, then
   `{"state":"ready"}` on `/health`. Docker is needed: if it is missing, installing it needs administrator rights,
   so ask. It runs on the processor, no graphics card needed: as a guide, a recent laptop (Core Ultra 9)
   transcribes about 16 times faster than real time and a small home server (Core i5) about 7 times. On an old or
   very small machine (2 cores, a Raspberry Pi), suggest running it on another computer of the home instead.

Any OpenAI-compatible server that returns words with `start` and `end` works too. One that returns only segments is
refused with a clear message.

## 4. Choose the languages and the translator, with the user

Ask in one short message:

- The languages of the subtitles they want (`target_languages`, for example `["fr", "es"]`). The spoken language is
  detected, and its own subtitles are always produced.
- How to translate, if at all:
  - **No translation**: subtitles in the spoken language only (translator `none`).
  - **A local model** already running (llama.cpp, Ollama, LM Studio…): type `openai`, its base address without
    `/v1` (llama.cpp `http://127.0.0.1:8080`, Ollama `http://127.0.0.1:11434`), its model name. Free; slower.
  - **The Claude API**: type `claude`, a paid key from the Anthropic Console stored as a reference (for example
    `env:ANTHROPIC_API_KEY`), and `pip install ".[claude]"`. A batch stops after transcription to show the
    estimated cost before spending anything.
  - **Another OpenAI-compatible API** (OpenAI, Mistral, Groq…): type `openai`, its base address, model, key reference.
- **Give a recommendation for the translator from what you measured in step 1**, with the reason, then let the
  user decide with full knowledge: it is advice, never a gate. A user whose machine is below the recommendation
  may still want to try a local model: help them, and say what to expect (speed, quality, memory). Translation quality depends mostly on the model; a local model is free and private but slower, an
  API is fast and better on idioms and names but paid and sends the subtitles text to the provider. Guide:

  | Machine | Recommendation |
  |---|---|
  | Graphics card with 8 GB of video memory or more, and 32 GB of RAM or more | A local "mixture of experts" model of about 30 to 35 billion parameters with about 3 billion active, quantized to 4 bits (Q4), run by llama.cpp with the experts in RAM and the rest on the card (llama.cpp `--fit on`, or `--n-cpu-moe`). Example measured on a laptop with an 8 GB RTX 5070 and 32 GB of RAM: Qwen3.6-35B-A3B Q4_K_XL, about 34 tokens/s, good translations. |
  | 16 GB of video memory or more (or Apple Silicon with 32 GB or more) | The same kind of model fully in video memory (faster), or a larger one. |
  | Smaller card, or 16 GB of RAM | A dense model of 7 to 9 billion parameters in Q4 works, slower and weaker on idioms: fine to try, an API for long batches. |
  | No graphics card, or under 16 GB of RAM | An API (Claude, or another OpenAI-compatible provider). |

  **Offer to measure instead of guessing**, if the user wants: with their agreement, start the candidate local
  model and translate a sample of about 25 subtitles (from a test video or a short `.srt` they provide) through
  its OpenAI-compatible API, then report the speed (seconds per subtitle, so the time for a whole video) and show a
  few translated lines so they judge the quality. With an API, the same sample costs about a cent: offer
  it the same way. Then the user picks, possibly both (a local model for long batches, an API for the episodes
  that matter).

  Prefer the newest models of these sizes over the names above, which date from November 2026. Installing a
  model server and downloading a model (several GB) is a change to the machine: ask first. With an API, a batch
  stops after transcription and shows the estimated cost, so the user decides before spending; suggest trying one
  short video first.
- Where the `.srt` files go: next to each video (players load them by themselves; the default) and/or in an output
  folder.

Dubbing is optional, off by default, and needs a graphics card and a separate Python environment (README,
"Dubbing"): mention it only if the user asks.

## 5. Write the settings

Start the web service, then write the settings through its API, which refuses a plain key:

```
.venv/bin/python -m subtitles.web                  # Windows: .venv/Scripts/python -m subtitles.web
curl -s http://127.0.0.1:4420/api/config           # current settings (defaults merged with the user's)
```

Change the returned object (keep the fields you do not touch) and send it back whole with
`curl -s -X PUT http://127.0.0.1:4420/api/config -H "Content-Type: application/json" --data @settings.json`. The
fields: `transcriptions` (list of `{id, name, url, key}`), `active_transcription`, `translators` (list of
`{id, type, name, url, model, key}`), `active_translator`, `target_languages`, `destination`
(`{next_to_video, output_dir}`), `output_dir`. The settings land in `config.json` in the user folder
(`%LOCALAPPDATA%\subtitles` on Windows, `~/.config/subtitles` elsewhere); `subtitles/config.toml` holds the
defaults and stays as it is. Delete your temporary `settings.json` afterwards.

## 6. Check every engine

```
curl -s -X POST http://127.0.0.1:4420/api/test -H "Content-Type: application/json" \
  -d '{"kind": "transcription", "engine": <the transcription entry>}'
curl -s -X POST http://127.0.0.1:4420/api/test -H "Content-Type: application/json" \
  -d '{"kind": "translator", "engine": <the translator entry>}'
```

Each answers `{"ok": true, …}`, or `{"ok": false, "detail": …}` with the reason. Fix what fails before going on.
Then, if the user agrees, subtitle a short video of theirs (a minute is enough) from the page
`http://127.0.0.1:4420/` and check that the `.srt` appears where expected.

## 7. Tell the user how to use it

- The page: `.venv/bin/python -m subtitles.web --open`. It listens on `127.0.0.1` only and stops by itself after
  60 minutes without activity.
- The command line: `.venv/bin/python -m subtitles "<video, folder, video or playlist address>"`; a folder or a
  playlist becomes a batch that resumes after an interruption.
- Downloads (Settings): the folder of the downloaded videos, the maximum quality (1080p by default) and other yt-dlp
  options, such as `--cookies-from-browser firefox` for videos that need an account.
- Where the settings are, and that **Settings** on the page changes them later.
- Offer, without insisting, a shortcut to start the page (a desktop shortcut, a shell alias): create it only if
  the user wants one.
