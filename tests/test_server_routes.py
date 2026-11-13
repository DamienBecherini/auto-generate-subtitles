"""Routes of the web server, through real HTTP requests (configuration and saving replaced)."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from subtitles import config
from subtitles.web import server


@pytest.fixture
def base(tmp_path, monkeypatch):
    cfg = {"output_dir": str(tmp_path / "out"), "transcriptions": [], "translators": [], "ui_language": "fr"}
    saved = []
    monkeypatch.setattr(config, "load", lambda *_a, **_k: json.loads(json.dumps(cfg)))
    monkeypatch.setattr(config, "save", lambda c, *_a, **_k: saved.append(c))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", saved
    httpd.shutdown()
    httpd.server_close()


def call(url, method="GET", body=None, raw=None, headers=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(url, data=data, method=method, headers={"X-Language": "fr", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers.get("Content-Type"), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read()


def js(url, method="GET", body=None, raw=None, headers=None):
    code, _type, data = call(url, method, body, raw, headers)
    return code, json.loads(data)


def test_page_static_files_and_catalogs(base):
    url, _ = base
    code, ctype, body = call(url + "/")
    assert code == 200 and ctype.startswith("text/html") and b"<html" in body.lower()
    assert js(url + "/static/missing.js")[0] == 404
    assert js(url + "/static/../server.py")[0] == 404
    code, ctype, body = call(url + "/locales/fr.json")
    assert code == 200 and ctype.startswith("application/json") and json.loads(body)
    assert js(url + "/locales/xx.json")[0] == 404


def test_read_routes(base):
    url, _ = base
    code, d = js(url + "/api/health")
    assert code == 200 and d["service"] == "subtitles" and d["contract"] == 2
    code, d = js(url + "/api/languages")
    assert code == 200 and "fr" in d["available"] and d["setting"] == "fr"
    code, d = js(url + "/api/status")
    assert code == 200 and d["local_ai"] is None and "task" in d
    assert js(url + "/api/config")[1]["ui_language"] == "fr"
    code, d = js(url + "/api/tasks")
    assert code == 200 and isinstance(d, list)
    assert js(url + "/api/batches")[0] == 200
    assert "last" in js(url + "/api/batches/last")[1]
    code, d = js(url + "/api/cleanup")
    assert code == 200 and d["root"].endswith(".work") and "options" in d


def test_unknown_identifiers_and_paths(base):
    url, _ = base
    for path, message in (("/api/tasks/ab12", "tâche"), ("/api/tasks/ab12/estimate", None), ("/api/batches/ab12", "lot"),
                          ("/api/batches/ab12/estimate", "lot"), ("/api/batches/ab12/review", "lot"),
                          ("/api/folder/ab12", None), ("/api/nothing", None), ("/api/tasks/XYZ", None)):
        code, d = js(url + path)
        assert code == 404 and d["error"], path
        if message:
            assert message in d["error"].lower(), (path, d)
    assert js(url + "/api/nothing", "POST", {})[0] == 404
    assert js(url + "/api/nothing", "PUT", {})[0] == 404
    assert js(url + "/api/health", "POST", {})[0] == 404


def test_actions_and_errors(base, tmp_path):
    url, saved = base
    code, d = js(url + "/api/tasks", "POST", {"source": "  "})
    assert code == 400 and d["error"]
    code, d = js(url + "/api/tasks", "POST", {"source": str(tmp_path)})
    assert code == 409 and d["folder"] is True
    assert js(url + "/api/tasks", "POST", raw=b"{not json")[0] == 400
    assert js(url + "/api/tasks/ab12/cancel", "POST", {}) == (200, {"ok": False})
    assert js(url + "/api/tasks/ab12/retry", "POST", {})[0] == 404
    assert js(url + "/api/tasks/ab12/translate", "POST", {})[0] == 409
    assert js(url + "/api/batches", "POST", {"videos": []})[0] == 400
    assert js(url + "/api/batches", "POST", {"videos": [{"path": "a.mp4"}], "stop": "nowhere"})[0] == 400
    for action in ("suspend", "resume", "cancel", "retry", "continue", "goal"):
        code, d = js(url + f"/api/batches/ab12/{action}", "POST", {})
        assert code == 409 and action in d["error"], action
    assert js(url + "/api/batches/ab12/translate", "POST", {"stop": "nowhere"})[0] == 400
    assert js(url + "/api/batches/ab12/validate", "POST", {"decisions": []})[0] == 404
    assert js(url + "/api/batches/load", "POST", {"file": str(tmp_path / "none.batch.json")})[0] == 400
    assert js(url + "/api/batches/last/forget", "POST", {}) == (200, {"ok": True})
    assert js(url + "/api/folder", "POST", {"path": str(tmp_path / "none")})[0] == 400
    assert js(url + "/api/folder/ab12/cancel", "POST", {}) == (200, {"ok": True})
    assert js(url + "/api/open", "POST", {"path": str(tmp_path / "none")})[0] == 404
    assert js(url + "/api/test", "POST", {"kind": "translator", "engine": {"type": "none"}})[1]["ok"] is True
    assert js(url + "/api/test", "POST", {"kind": "other"})[0] == 400
    assert js(url + "/api/local-transcription/stop", "POST", {}) == (200, {"ok": True})
    code, d = js(url + "/api/cleanup", "POST", {"options": {}})
    assert code == 200 and "deleted" in d
    code, d = js(url + "/api/config", "PUT", {"ui_language": "fr", "x": 1})
    assert code == 200 and saved == [{"ui_language": "fr", "x": 1}]


def test_upload_of_a_file(base, tmp_path):
    url, _ = base
    assert js(url + "/api/files", "PUT", raw=b"abc")[0] == 400
    code, d = js(url + "/api/files?name=sub%20t.srt", "PUT", raw=b"hello")
    assert code == 200 and d["existed"] is False and (tmp_path / "out" / "sub t.srt").read_bytes() == b"hello"
    assert js(url + "/api/files?name=sub%20t.srt", "PUT", raw=b"world")[1]["existed"] is True


def test_query_parameter_of_wrong_type_is_a_bad_request(base):
    url, _ = base
    assert js(url + "/api/tasks/ab12?since=abc")[0] == 400


def test_router_parameters_and_order():
    fn, params = server.resolve("GET", "/api/tasks/ab12")
    assert fn is server.get_task and params == {"id": "ab12"}
    assert server.resolve("GET", "/api/batches/last")[0] is server.last_batch
    assert server.resolve("POST", "/api/batches/ab12/cancel") == (server.suspend_or_cancel_batch, {"id": "ab12", "action": "cancel"})
    assert server.resolve("POST", "/api/batches/ab12/explode") is None
    assert server.resolve("GET", "/api/tasks/AB12") is None          # identifiers are lowercase hexadecimal
    assert server.resolve("DELETE", "/api/tasks/ab12") is None
    assert server.resolve("GET", "/static/css/app.css") == (server.static_file, {"name": "css/app.css"})
    routes = [(m, r.pattern) for m, r, _fn in server.ROUTES]
    assert len(routes) == len(set(routes)), "route declared twice"


def test_a_page_of_another_site_cannot_use_the_api(base, tmp_path):
    """CSRF: a site open in the browser posts to the API (text/plain: no preflight). Here, a command to run."""
    url, saved = base
    marker = tmp_path / "run.txt"
    engine = {"url": "http://127.0.0.1:9", "command": ["python", "-c", f"open(r'{marker}', 'w')"]}
    for origin in ("https://evil.example", "null", "http://127.0.0.1:1"):
        code, d = js(url + "/api/test", "POST", raw=json.dumps({"kind": "transcription", "engine": engine}).encode(),
                     headers={"Content-Type": "text/plain", "Origin": origin})
        assert code == 403 and "refusée" in d["error"], origin
    assert js(url + "/api/config", "PUT", {"x": 1}, headers={"Origin": "https://evil.example"})[0] == 403
    assert not marker.exists() and saved == []


def test_dns_rebinding_is_refused(base):
    """A site whose name now points to 127.0.0.1: same origin for the browser, but its name in Host."""
    url, _ = base
    assert js(url + "/api/config", headers={"Host": "evil.example"})[0] == 403
    assert js(url + "/api/tasks/ab12/cancel", "POST", {}, headers={"Host": "evil.example", "Origin": "http://evil.example"})[0] == 403


def test_the_page_itself_and_other_clients_are_accepted(base):
    url, _ = base
    port = url.rsplit(":", 1)[1]
    for headers in ({"Origin": url}, {"Origin": f"http://localhost:{port}", "Host": f"localhost:{port}"}, {}):
        assert js(url + "/api/tasks/ab12/cancel", "POST", {}, headers=headers) == (200, {"ok": False}), headers
