"""Optional readable copy of every saved clip, filed by person and sign.

When PERSONAL_SIGNS_TEAM_CLIPS_DIR is set, each clip that the engine saves is also copied to

    <dir>/<Person's name>/<sign>/<sign>_sample_01.webm, _02, ...

so the project owner can browse the team's recordings directly. The engine's own storage (by
account id) is unchanged. Undoing a clip in the app removes its copy too. Each person's folder
holds an _owner.json that ties it to the account, so two people with the same name never share
a folder and a later name change keeps the same folder.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

DIR_ENV = "PERSONAL_SIGNS_TEAM_CLIPS_DIR"
OWNER_FILE = "_owner.json"
_lock = threading.Lock()
_UNSAFE = re.compile(r"[^A-Za-z0-9 ._-]+")


def _clean(text: str, fallback: str) -> str:
    cleaned = re.sub(r"\s+", " ", _UNSAFE.sub("_", text or "")).strip(" ._")
    return cleaned[:50] or fallback


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def _person_dir(root: Path, user_id: str, name: str, email: str) -> Path:
    """The folder that belongs to this account, created on first use."""
    for owner_file in root.glob(f"*/{OWNER_FILE}"):
        if _read(owner_file).get("owner") == user_id:
            return owner_file.parent
    base = _clean(name, "") or _clean(email.split("@")[0], "") or "user"
    folder, n = root / base, 2
    while folder.exists():
        folder, n = root / f"{base} ({n})", n + 1
    folder.mkdir(parents=True)
    _write(folder / OWNER_FILE, {"owner": user_id, "name": name, "email": email, "samples": {}})
    return folder


def save(root: Path, user_id: str, name: str, email: str, label: str, sample_id: str, clip: bytes) -> str:
    """Copy one saved clip; returns its path relative to root."""
    with _lock:
        person = _person_dir(root, user_id, name, email)
        sign = _clean(label, "sign")
        sign_dir = person / sign
        sign_dir.mkdir(exist_ok=True)
        numbers = [int(m.group(1)) for p in sign_dir.iterdir()
                   if (m := re.fullmatch(re.escape(sign) + r"_sample_(\d+)\.\w+", p.name))]
        extension = ".webm" if clip.startswith(b"\x1a\x45\xdf\xa3") else ".mp4"
        target = sign_dir / f"{sign}_sample_{max(numbers, default=0) + 1:02d}{extension}"
        target.write_bytes(clip)
        owner = _read(person / OWNER_FILE)
        owner.update(owner=user_id, name=name or owner.get("name", ""), email=email or owner.get("email", ""))
        owner.setdefault("samples", {})[sample_id] = f"{sign}/{target.name}"
        _write(person / OWNER_FILE, owner)
        return str(target.relative_to(root))


def remove(root: Path, user_id: str, sample_id: str) -> None:
    """Delete the copy of a clip that the person undid in the app."""
    with _lock:
        for owner_file in root.glob(f"*/{OWNER_FILE}"):
            owner = _read(owner_file)
            if owner.get("owner") != user_id:
                continue
            relative = owner.get("samples", {}).pop(sample_id, None)
            if relative:
                (owner_file.parent / relative).unlink(missing_ok=True)
                _write(owner_file, owner)
            return


def root_dir(environ) -> Path | None:
    value = (environ.get(DIR_ENV) or "").strip()
    return Path(value) if value else None
