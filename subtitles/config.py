"""Configuration: package defaults (subtitles/config.toml) overridden by the user settings (config.json in the user
folder, written by the web interface). Keys are never stored in clear text: a reference "env:<NAME>",
"file:<path>" (file that holds the key) or "sops:<file>#<NAME>" (if sops is installed)."""
import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

from . import i18n

DEFAULTS_FILE = Path(__file__).resolve().parent / "config.toml"


def user_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "subtitles"


USER_FILE = user_dir() / "config.json"
REFERENCES = ("env:", "file:", "sops:")


class ConfigError(Exception):
    pass


def load(defaults_file: Path = DEFAULTS_FILE, user_file: Path | None = USER_FILE) -> dict:
    with open(defaults_file, "rb") as f:
        cfg = tomllib.load(f)
    if user_file and user_file.exists():
        own = json.loads(user_file.read_text(encoding="utf-8"))
        cfg = merge(cfg, own)
    if cfg.get("output_dir"):
        cfg["output_dir"] = os.path.expanduser(cfg["output_dir"])
    return cfg


def merge(base: dict, over: dict) -> dict:
    """Tables are merged key by key; lists (engines) and plain values are replaced."""
    r = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(r.get(k), dict):
            r[k] = merge(r[k], v)
        else:
            r[k] = v
    return r


def save(cfg: dict, file: Path = USER_FILE):
    for m in cfg.get("transcriptions", []) + cfg.get("translators", []):
        key = str(m.get("key", ""))
        if key and not key.startswith(REFERENCES):
            raise ConfigError(i18n.t("err.key_value_refused", name=m.get("name", m.get("id")), forms=i18n.t("cfg.key_forms")))
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")


def engine_name(m: dict) -> str:
    """Displayed name of an engine: its `name` field, else a translated name from its id (bundled engines), else the id."""
    return m.get("name") or i18n.label("engine.", str(m.get("id", "")))


def engine(cfg: dict, list_key: str, active_key: str, ident: str | None = None) -> dict:
    """Entry of cfg[list_key] whose id is `ident` (or cfg[active_key])."""
    wanted = ident or cfg.get(active_key)
    for m in cfg.get(list_key, []):
        if m.get("id") == wanted:
            return m
    raise ConfigError(i18n.t("err.not_in_list", id=wanted, list=list_key))


def resolve_key(reference: str) -> str:
    """Value of a key from its reference. Never logged by the callers."""
    reference = (reference or "").strip()
    if not reference:
        return ""
    if reference.startswith("env:"):
        return os.environ.get(reference[4:], "")
    if reference.startswith("file:"):
        path = Path(os.path.expanduser(reference[5:]))
        try:
            return path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as e:
            raise ConfigError(i18n.t("err.key_file_unreadable", path=path, error=type(e).__name__)) from e
    if reference.startswith("sops:"):
        file, _, name = reference[5:].partition("#")
        if not name:
            raise ConfigError(i18n.t("err.sops_no_name", ref=reference))
        sops = shutil.which("sops")
        if not sops:
            raise ConfigError(i18n.t("err.sops_missing"))
        r = subprocess.run([sops, "-d", "--extract", f'["{name}"]', file], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise ConfigError(i18n.t("err.sops_failed", name=name, file=file, detail=r.stderr.strip()[-200:]))
        return r.stdout.strip()
    raise ConfigError(i18n.t("err.plain_key", forms=i18n.t("cfg.key_forms")))
