from subtitles import folder


def tree(tmp_path):
    for s in ("Season 2", "Season 10"):
        (tmp_path / s).mkdir()
        (tmp_path / s / "E01.mp4").write_bytes(b"x")
    (tmp_path / "Season 2" / "E01.fr.srt").write_text("1", encoding="utf-8")


def test_scan_natural_sort_and_subtitles(tmp_path):
    tree(tmp_path)
    v = folder.scan(tmp_path)
    assert [x["folder"] for x in v] == ["Season 2", "Season 10"]
    assert v[0]["subtitles"] == ["fr"] and v[1]["subtitles"] == []


def test_full_scan(tmp_path, monkeypatch):
    tree(tmp_path)
    monkeypatch.setattr(folder, "track_language", lambda p: "en")
    a = folder.FolderScan(tmp_path)
    a.run()
    d = a.to_dict(True)
    assert d["state"] == "done" and d["count"] == 2 and d["done"] == 2 and len(d["videos"]) == 2
    assert set(d["languages"].values()) == {"en"} and not d["languages_interrupted"]


def test_list_absent_from_progress_unless_asked(tmp_path, monkeypatch):
    tree(tmp_path)
    monkeypatch.setattr(folder, "track_language", lambda p: "")
    a = folder.FolderScan(tmp_path)
    a.run()
    assert "videos" not in a.to_dict() and a.to_dict()["languages"] == {}


def test_cancel_during_scan(tmp_path):
    tree(tmp_path)
    a = folder.FolderScan(tmp_path)
    a.cancel()
    a.run()
    assert a.to_dict()["state"] == "cancelled" and a.videos is None


def test_cancel_during_languages_keeps_the_list(tmp_path, monkeypatch):
    tree(tmp_path)
    a = folder.FolderScan(tmp_path, workers=1)

    def read(p):
        a.cancel()
        return "en"

    monkeypatch.setattr(folder, "track_language", read)
    a.run()
    d = a.to_dict(True)
    assert d["state"] == "done" and d["count"] == 2 and d["done"] == 1 and d["languages_interrupted"]


def test_existing_batch_reported(tmp_path):
    (tmp_path / folder.BATCH_FILE_NAME).write_text("{}", encoding="utf-8")
    assert folder.FolderScan(tmp_path).existing_batch.endswith(folder.BATCH_FILE_NAME)
