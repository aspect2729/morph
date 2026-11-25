"""Recent documents for the PDF editor (like Acrobat's Home → Recent).

Each entry keeps a PDF copy (the latest saved version, or the original if never saved),
a first-page thumbnail and a little metadata. On the Mac there is one library; on a public
server every visitor's workspace has its own, which nobody else can list or open.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

import pymupdf

from . import config

_lock = threading.Lock()
_ID = re.compile(r"^[0-9a-f]{12}$")


def _root(ws: str) -> Path:
    if not config.WEB:
        return config.LIBRARY
    if not re.fullmatch(r"[0-9a-f]{32}", ws or ""):
        raise PermissionError("bad workspace")
    return config.LIBRARY / "ws" / ws


def _paths(ws: str):
    r = _root(ws)
    return r, r / "docs", r / "thumbs", r / "index.json"


def _load(ws: str) -> list[dict]:
    try:
        return json.loads(_paths(ws)[3].read_text())
    except Exception:
        return []


def _save(ws: str, items: list[dict]) -> None:
    root, _, _, index = _paths(ws)
    root.mkdir(parents=True, exist_ok=True)
    tmp = index.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=1))
    tmp.replace(index)


def _thumb(pdf: Path, out: Path) -> int:
    doc = pymupdf.open(pdf)
    page = doc[0]
    z = 360 / max(1, page.rect.width)
    page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False).save(str(out), jpg_quality=82)
    return doc.page_count


def remember(ws: str, pdf: Path, name: str) -> str:
    """Add a document to Recent (re-opening the same file updates its entry). Returns its id."""
    root, docs, thumbs, _ = _paths(ws)
    docs.mkdir(parents=True, exist_ok=True)
    thumbs.mkdir(parents=True, exist_ok=True)
    size = pdf.stat().st_size
    with _lock:
        items = _load(ws)
        same = next((i for i in items if i["name"] == name and i.get("orig_size") == size), None)
        if same:
            same["opened"] = time.time()
            items.remove(same); items.insert(0, same)
            _save(ws, items)
            return same["id"]
        lid = uuid.uuid4().hex[:12]
        shutil.copy(pdf, docs / f"{lid}.pdf")
        pages = _thumb(pdf, thumbs / f"{lid}.jpg")
        now = time.time()
        items.insert(0, {"id": lid, "name": name, "pages": pages, "size": size, "orig_size": size,
                         "created": now, "updated": now, "opened": now, "edited": False})
        for old in items[config.LIBRARY_MAX:]:
            _drop_files(ws, old["id"])
        _save(ws, items[:config.LIBRARY_MAX])
        return lid


def update(ws: str, lid: str, pdf: Path, name: str | None = None) -> None:
    """A document was saved: keep the edited version as the one Recent re-opens."""
    if not _ID.match(lid or ""):
        return
    _, docs, thumbs, _ = _paths(ws)
    with _lock:
        items = _load(ws)
        it = next((i for i in items if i["id"] == lid), None)
        if not it:
            return
        shutil.copy(pdf, docs / f"{lid}.pdf")
        it["pages"] = _thumb(pdf, thumbs / f"{lid}.jpg")
        it["size"] = pdf.stat().st_size
        it["updated"] = it["opened"] = time.time()
        it["edited"] = True
        if name:
            it["name"] = name if name.lower().endswith(".pdf") else name + ".pdf"
        items.remove(it); items.insert(0, it)
        _save(ws, items)


def listing(ws: str) -> list[dict]:
    _, docs, _, _ = _paths(ws)
    with _lock:
        items = [i for i in _load(ws) if (docs / f"{i['id']}.pdf").exists()]
    return sorted(items, key=lambda i: max(i.get("opened", 0), i.get("updated", 0)), reverse=True)


def path(ws: str, lid: str) -> tuple[Path, str]:
    if not _ID.match(lid or ""):
        raise FileNotFoundError(lid)
    _, docs, _, _ = _paths(ws)
    it = next((i for i in _load(ws) if i["id"] == lid), None)
    p = docs / f"{lid}.pdf"
    if not it or not p.exists():
        raise FileNotFoundError(lid)
    return p, it["name"]


def thumb(ws: str, lid: str) -> Path | None:
    if not _ID.match(lid or ""):
        return None
    t = _paths(ws)[2] / f"{lid}.jpg"
    return t if t.exists() else None


def touch(ws: str, lid: str) -> None:
    with _lock:
        items = _load(ws)
        it = next((i for i in items if i["id"] == lid), None)
        if it:
            it["opened"] = time.time()
            _save(ws, items)


def forget(ws: str, lid: str) -> None:
    if not _ID.match(lid or ""):
        return
    with _lock:
        items = [i for i in _load(ws) if i["id"] != lid]
        _save(ws, items)
    _drop_files(ws, lid)


def _drop_files(ws: str, lid: str) -> None:
    _, docs, thumbs, _ = _paths(ws)
    for p in (docs / f"{lid}.pdf", thumbs / f"{lid}.jpg"):
        p.unlink(missing_ok=True)


def expire_workspaces() -> int:
    """Public mode: delete workspaces nobody has used for LIBRARY_DAYS. Returns how many."""
    if not config.WEB:
        return 0
    base = config.LIBRARY / "ws"
    if not base.exists():
        return 0
    cutoff = time.time() - config.LIBRARY_DAYS * 86400
    n = 0
    for d in base.iterdir():
        try:
            idx = d / "index.json"
            last = idx.stat().st_mtime if idx.exists() else d.stat().st_mtime
            if last < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                n += 1
        except FileNotFoundError:
            pass
    return n
