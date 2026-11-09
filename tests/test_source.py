import os
from pathlib import Path

import pytest

from soustitres import source


def test_slug():
    assert source.slug("Agents IA en LOCAL de ZÉRO à PRO en 2h00_[1080p_opus]") == "Agents-IA-en-LOCAL-de-ZERO-a-PRO-en-2h00-1080p-opus"
    assert source.slug("   ") == "video"


@pytest.mark.skipif(os.name != "nt", reason="conversion propre à Windows")
def test_chemin_local_depuis_wsl():
    assert source.chemin_local("/mnt/d/videos/a b.mp4", ["wsl", "-e", "yt-dlp"]) == Path("D:/videos/a b.mp4")
    assert source.chemin_local("D:/x/y.mp4", ["yt-dlp"]) == Path("D:/x/y.mp4")
    with pytest.raises(source.ErreurSource):
        source.chemin_local("/home/moi/inexistant/a.mp4", ["wsl", "-e", "yt-dlp"])
