# Changelog

## 0.1.0 (unreleased)

First public version.

- Web interface and command line: one video, or a whole folder as a resumable batch.
- Transcription through any OpenAI-compatible server that returns word timestamps.
- Translation through a local LLM, the Claude API, any OpenAI-compatible API, or none.
- File pickers and "open folder" on Windows, macOS and Linux.
- Batches run several translations at once through a remote API, and show the time per video.
- Choose at the start: subtitles only, or subtitles with dubbing. Step-by-step batches, with a button to take one episode to the end first.
- AI review of proper names, validated by you before translation, and a glossary shared by the batch.
- Optional voice dubbing (off by default) and a multi-track MKV next to the video, which can replace the original once checked.
- Subtitle files edited by hand are no longer overwritten.
- Health endpoint following the "local service" contract: busy state, activity, remaining time, idle shutdown.
