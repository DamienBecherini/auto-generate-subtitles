import os
from pathlib import Path

import pytest

from subtitles import source


def test_slug():
    assert source.slug("Agents IA en LOCAL de ZÉRO à PRO en 2h00_[1080p_opus]") == "Agents-IA-en-LOCAL-de-ZERO-a-PRO-en-2h00-1080p-opus"
    assert source.slug("   ") == "video"


@pytest.mark.skipif(os.name != "nt", reason="conversion specific to Windows")
def test_local_path_from_wsl():
    assert source.local_path("/mnt/d/videos/a b.mp4", ["wsl", "-e", "yt-dlp"]) == Path("D:/videos/a b.mp4")
    assert source.local_path("D:/x/y.mp4", ["yt-dlp"]) == Path("D:/x/y.mp4")
    with pytest.raises(source.SourceError):
        source.local_path("/home/me/missing/a.mp4", ["wsl", "-e", "yt-dlp"])
