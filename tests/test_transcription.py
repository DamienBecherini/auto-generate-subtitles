"""Client de transcription face à un faux serveur HTTP : ce qui est demandé, et les variantes de réponse."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from soustitres import decoupe, transcription

MOTS = [{"word": " Bonjour", "start": 0.0, "end": 0.5}, {"word": " à", "start": 0.6, "end": 0.7}]


def serveur(tmp_path, reponse, code=200, brut=None):
    """Renvoie (url, requêtes reçues)."""
    recues = []

    class Gestion(BaseHTTPRequestHandler):
        def do_POST(self):
            recues.append((self.path, dict(self.headers), self.rfile.read(int(self.headers["Content-Length"]))))
            self.repondre()

        def do_GET(self):
            recues.append((self.path, dict(self.headers), b""))
            self.repondre()

        def repondre(self):
            corps = brut if brut is not None else json.dumps(reponse).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            self.wfile.write(corps)

        def log_message(self, *_):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Gestion)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_port}", recues, httpd


@pytest.fixture
def audio(tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(b"RIFFdonnees")
    return f


def test_les_mots_horodates_sont_demandes(tmp_path, audio):
    url, recues, httpd = serveur(tmp_path, {"language": "fr", "words": MOTS, "segments": []})
    try:
        transcription.transcrire(url, audio, "fr", cle="k")
    finally:
        httpd.shutdown()
    chemin, entetes, corps = recues[0]
    assert chemin == "/v1/audio/transcriptions"
    assert b'name="response_format"\r\n\r\nverbose_json' in corps
    assert b'name="timestamp_granularities[]"\r\n\r\nword' in corps
    assert b'name="language"\r\n\r\nfr' in corps
    assert entetes["Authorization"] == "Bearer k"


def test_reponse_whisper_utilisable_par_la_decoupe(tmp_path, audio):
    url, _, httpd = serveur(tmp_path, {"language": "french", "words": MOTS, "segments": []})
    try:
        r = transcription.transcrire(url, audio)
    finally:
        httpd.shutdown()
    assert [m.texte for m in decoupe.depuis_json(r["words"])] == ["Bonjour", "à"]
    assert r["language"] == ""


@pytest.mark.parametrize("code", ["fr", "pt-BR", "yue"])
def test_code_de_langue_garde(tmp_path, audio, code):
    url, _, httpd = serveur(tmp_path, {"language": code, "words": MOTS})
    try:
        assert transcription.transcrire(url, audio)["language"] == code
    finally:
        httpd.shutdown()


def test_serveur_sans_mots_explique_l_exigence(tmp_path, audio):
    url, _, httpd = serveur(tmp_path, {"text": "Bonjour", "segments": []})
    try:
        with pytest.raises(transcription.ErreurTranscription, match="timestamp_granularities"):
            transcription.transcrire(url, audio)
    finally:
        httpd.shutdown()


def test_sante_annoncee(tmp_path):
    url, _, httpd = serveur(tmp_path, {"etat": "pret"})
    try:
        assert transcription.sante(url) == "prêt"          # état annoncé, en clair dans la langue des messages
    finally:
        httpd.shutdown()


@pytest.mark.parametrize("options", [{"code": 404, "reponse": {}}, {"reponse": None, "brut": b"OK"}, {"reponse": ["ok"]}, {"reponse": {"message": "OK"}}])
def test_serveur_sans_route_de_sante_n_est_pas_une_erreur(tmp_path, options):
    url, _, httpd = serveur(tmp_path, **options)
    try:
        assert transcription.sante(url).startswith("répond")
    finally:
        httpd.shutdown()


def test_serveur_injoignable_reste_une_erreur():
    with pytest.raises(transcription.ErreurTranscription, match="injoignable"):
        transcription.sante("http://127.0.0.1:9", delai_s=2)
