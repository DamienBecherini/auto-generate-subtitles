"""Dialog boxes and folder opening outside Windows: commands built from the system and the tools present."""
import pytest

from subtitles.web import server


def having(*tools):
    return lambda name: f"/usr/bin/{name}" if name in tools else None


def test_zenity_video_file_with_filter_and_start():
    c = server.dialog_command("file", "/data/videos", "linux", having("zenity"))
    assert c[:2] == ["zenity", "--file-selection"]
    assert "--filename=/data/videos/" in c
    assert any(a.startswith("--file-filter=Vidéos") and "*.mkv" in a for a in c)
    assert "--directory" not in c


def test_zenity_folder():
    c = server.dialog_command("folder", "", "linux", having("zenity", "kdialog"))
    assert c[0] == "zenity" and "--directory" in c and not any(a.startswith("--filename") for a in c)


def test_kdialog_if_no_zenity():
    assert server.dialog_command("batch", "/x", "linux", having("kdialog"))[3:] == ["--getopenfilename", "/x", "*.batch.json|Fichier de lot"]
    assert server.dialog_command("source_folder", "", "linux", having("kdialog"))[3:] == ["--getexistingdirectory", "."]


def test_no_tool():
    assert server.dialog_command("file", "", "linux", having()) is None


def test_macos_path_passed_as_argument_not_in_the_script():
    start = '/a"; do shell script "x'
    c = server.dialog_command("file", start, "darwin", having())
    assert c[:2] == ["osascript", "-e"] and "choose file" in c[2]
    assert start not in c[2] and c[-1] == start
    assert "choose folder" in server.dialog_command("folder", "", "darwin", having())[2]


def test_without_tool_useful_message(monkeypatch):
    monkeypatch.setattr(server, "dialog_command", lambda *_: None)
    with pytest.raises(server.RequestError, match="saisir le chemin"):
        server._unix_dialog("file", "")


@pytest.mark.parametrize("code, out, expected", [(0, "/data/film.mp4\n", "/data/film.mp4"), (0, "/data/dossier/\n", "/data/dossier"), (1, "", "")])
def test_dialog_result(monkeypatch, code, out, expected):
    monkeypatch.setattr(server, "dialog_command", lambda *_: ["tool"])
    monkeypatch.setattr(server.subprocess, "run", lambda *_a, **_k: type("R", (), {"returncode": code, "stdout": out})())
    assert server._unix_dialog("file", "") == expected


def test_open_depending_on_the_system(tmp_path):
    f = tmp_path / "a.srt"
    f.write_text("x", encoding="utf-8")
    assert server.open_command(f, "win32") == ["explorer", "/select,", str(f)]
    assert server.open_command(tmp_path, "win32") == ["explorer", str(tmp_path)]
    assert server.open_command(f, "darwin") == ["open", "-R", str(f)]
    assert server.open_command(tmp_path, "darwin") == ["open", str(tmp_path)]
    assert server.open_command(f, "linux") == ["xdg-open", str(tmp_path)]
