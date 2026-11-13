import os
import time

from subtitles import cleanup, language, translation


def _files(work):
    names = ["v.audio.m4a", "v.en.srt", "v.fr.srt", "v.fr.vtt", "transcription.json", "translation.fr.json", "translation.de.json",
             "log-20261101-1000-a1.txt", "log-20261108-1000-b2.txt"]
    for n in names:
        (work / n).write_text("x" * 10, encoding="utf-8")
    return names


def test_classify(tmp_path):
    _files(tmp_path)
    c = {k: sorted(p.name for p in v) for k, v in cleanup.classify(tmp_path).items()}
    assert c["audio"] == ["v.audio.m4a"]
    assert c["subtitles"] == ["v.en.srt", "v.fr.srt", "v.fr.vtt"]
    assert c["transcription"] == ["transcription.json", "translation.de.json", "translation.fr.json"]
    assert c["logs"] == ["log-20261101-1000-a1.txt", "log-20261108-1000-b2.txt"]


def test_clean_folder_by_default_keeps_transcription_and_logs(tmp_path):
    _files(tmp_path)
    count, size = cleanup.clean_folder(tmp_path, cleanup.DEFAULTS)
    assert count == 4 and size == 40
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == ["log-20261101-1000-a1.txt", "log-20261108-1000-b2.txt", "transcription.json", "translation.de.json", "translation.fr.json"]


def test_old_logs_only(tmp_path):
    _files(tmp_path)
    old = tmp_path / "log-20261101-1000-a1.txt"
    three_days_ago = time.time() - 72 * 3600
    os.utime(old, (three_days_ago, three_days_ago))
    opts = {**cleanup.DEFAULTS, "audio": False, "subtitles": False, "logs": True, "logs_age_h": 48}
    count, _ = cleanup.clean_folder(tmp_path, opts)
    assert count == 1 and not old.exists() and (tmp_path / "log-20261108-1000-b2.txt").exists()


def test_keep_protects_a_file(tmp_path):
    _files(tmp_path)
    cleanup.clean_folder(tmp_path, cleanup.DEFAULTS, keep={tmp_path / "v.fr.srt"})
    assert (tmp_path / "v.fr.srt").exists() and not (tmp_path / "v.en.srt").exists()


def test_summary_and_clean_all(tmp_path):
    root = tmp_path / ".work"
    for d in ("a", "b"):
        (root / d).mkdir(parents=True)
        _files(root / d)
    b = cleanup.summary(root)
    assert b["audio"] == {"files": 2, "bytes": 20} and b["subtitles"]["files"] == 6
    r = cleanup.clean_all(root, {**cleanup.DEFAULTS, "transcription": True, "logs": True, "logs_age_h": 0})
    assert r["audio"]["files"] == 2 and r["transcription"] == {"files": 6, "bytes": 60} and r["logs"]["files"] == 4
    assert not root.exists() or not any(root.iterdir())   # empty folders deleted


def test_purge_logs(tmp_path):
    root = tmp_path / ".work"
    (root / "a").mkdir(parents=True)
    _files(root / "a")
    old = root / "a" / "log-20261101-1000-a1.txt"
    t = time.time() - 100 * 3600
    os.utime(old, (t, t))
    assert cleanup.purge_logs(root, 48) == (1, 10)
    assert cleanup.purge_logs(root, 48) == (0, 0)


def test_language_names_and_codes():
    assert language.name("fr") == "français" and language.name("en") == "anglais"
    assert "zz" in language.name("zz")
    assert language.normalize_codes("fr, EN;de fr") == ["fr", "en", "de"]
    assert language.normalize_codes(["", " It "]) == ["it"]


def test_instructions_per_language():
    assert "Tu traduis en français." in translation.instructions("fr")
    assert "Tu traduis en allemand." in translation.instructions("de")
    assert "{langue}" not in translation.instructions("pl")
