#!/usr/bin/env python3
"""Morph smoke test — run it against a running server before (and after) every deploy.

    python3 scripts/smoke_test.py http://127.0.0.1:7788          # the Mac app
    python3 scripts/smoke_test.py https://your-domain --web      # a public server (also checks privacy)

No third-party packages needed. Exits non-zero if anything fails.
"""
from __future__ import annotations

import http.cookiejar
import io
import json
import struct
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
import zlib

# Windows consoles default to a legacy code page that cannot print the ✓/→ characters used below
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass
BASE = (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "http://127.0.0.1:7788").rstrip("/")
WEB = "--web" in sys.argv
FAILS: list[str] = []


class Client:
    def __init__(self):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def req(self, method, path, data=None, ctype=None, raw=False):
        r = urllib.request.Request(BASE + path, data=data, method=method)
        # Cloudflare-fronted hosts (e.g. Antideploy) reject Python's default User-Agent with "error code: 1010"
        r.add_header("User-Agent", "Mozilla/5.0 (Macintosh) MorphSmokeTest/1.0")
        if ctype:
            r.add_header("Content-Type", ctype)
        try:
            with self.op.open(r, timeout=300) as resp:
                body = resp.read()
                return resp.status, (body if raw else (json.loads(body) if body[:1] in b"{[" else body))
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def get(self, path, raw=False):
        return self.req("GET", path, raw=raw)

    def post(self, path, obj=None):
        return self.req("POST", path, json.dumps(obj or {}).encode(), "application/json")

    def upload(self, path, name, data):
        b = uuid.uuid4().hex
        body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
        return self.req("POST", path, body, f"multipart/form-data; boundary={b}")


def check(name, ok, detail=""):
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


# ---------------------------------------------------------------- test files (generated, no dependencies)

def make_pdf(text="Quarterly Report", body="Revenue grew 24% this quarter.") -> bytes:
    content = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET BT /F1 12 Tf 72 690 Td ({body}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offs.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    x = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for o in offs:
        out.write(b"%010d 00000 n \n" % o)
    out.write(b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x))
    return out.getvalue()


def make_png(w=120, h=80) -> bytes:
    raw = b"".join(b"\x00" + b"".join(bytes((x * 2 % 256, y * 3 % 256, 160)) for x in range(w))
                   for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def make_docx(text="Hello from the Morph smoke test.") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return buf.getvalue()


def convert(c: Client, name, data, target, timeout=240):
    st, up = c.upload("/api/upload", name, data)
    if st != 200:
        return st, None, up
    st, j = c.post("/api/convert", {"id": up["id"], "target": target})
    if st != 200:
        return st, None, j
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, s = c.get(f"/api/jobs/{j['job']}")
        if s.get("status") != "running":
            break
        time.sleep(0.7)
    if s.get("status") != "done":
        return 500, None, s
    st, blob = c.get(f"/api/download/{j['job']}", raw=True)
    return st, (up, j["job"], blob), s


def main():
    print(f"Morph smoke test → {BASE}")
    a, b = Client(), Client()
    st, h = a.get("/api/health")
    check("server is up", st == 200 and h.get("ok"), str(h)[:120])
    eng = h.get("engines", {}) if isinstance(h, dict) else {}
    for k in ("libreoffice", "pandoc", "ffmpeg", "tesseract"):   # Ghostscript is optional (PyMuPDF compresses)
        check(f"engine: {k}", eng.get(k), "missing on server")
    if WEB:
        check("server runs in public mode", h.get("mode") == "web", h.get("mode"))

    print("Conversions")
    st, r, info = convert(a, "letter.docx", make_docx(), "pdf")
    check("Word → PDF (LibreOffice)", st == 200 and r and r[2][:5] == b"%PDF-", str(info)[:160])
    first = r
    st, r, info = convert(a, "report.pdf", make_pdf(), "docx")
    check("PDF → Word", st == 200 and r and r[2][:2] == b"PK", str(info)[:160])
    st, r, info = convert(a, "report.pdf", make_pdf(), "pdf_png")
    check("PDF → PNG", st == 200 and r and r[2][:4] == b"\x89PNG", str(info)[:160])
    page_png = r[2] if r else None
    st, r, info = convert(a, "pic.png", make_png(), "jpg")
    check("PNG → JPG", st == 200 and r and r[2][:2] == b"\xff\xd8", str(info)[:160])
    st, r, info = convert(a, "data.csv", b"name,score\nAnn,91\nBob,78\n", "xlsx")
    check("CSV → Excel", st == 200 and r and r[2][:2] == b"PK", str(info)[:160])
    st, r, info = convert(a, "notes.md", b"# Notes\n\nHello **world**\n", "pdf")
    check("Markdown → PDF (Pandoc + LibreOffice)", st == 200 and r and r[2][:5] == b"%PDF-", str(info)[:160])

    print("PDF editor")
    st, s = a.post("/api/pdf/session")
    sid = s.get("sid") if isinstance(s, dict) else None
    check("open editing session", st == 200 and sid)
    st, d = a.upload(f"/api/pdf/{sid}/add?remember=1", "report.pdf", make_pdf())
    check("open a PDF", st == 200 and isinstance(d, dict) and d.get("pages"), str(d)[:160])
    src = d.get("src") if isinstance(d, dict) else ""
    st, L = a.get(f"/api/pdf/{sid}/lines/{src}/0")
    lines = L.get("lines", []) if isinstance(L, dict) else []
    check("read text lines", st == 200 and any("Quarterly" in l["text"] for l in lines), str(L)[:160])
    line = next((l for l in lines if "Quarterly" in l["text"]), None)
    edits = [{"type": "text_edit", "line": line, "text": "Annual Report", "size": line["size"], "color": line["color"],
              "family": line["family"], "bold": line["bold"], "italic": line["italic"]}] if line else []
    st, sv = a.post(f"/api/pdf/{sid}/save", {"pages": [{"src": src, "index": 0, "rotate": 0, "edits": edits}],
                                             "name": "edited", "lib": d.get("lib") if isinstance(d, dict) else None})
    ok = st == 200 and isinstance(sv, dict)
    if ok:
        _, pdf = a.get(f"/api/download/{sv['job']}", raw=True)
        ok = pdf[:5] == b"%PDF-"
    check("edit text and save PDF", ok, str(sv)[:160])
    if page_png:
        st, d2 = a.upload(f"/api/pdf/{sid}/add", "scan.png", page_png)
        src2 = d2.get("src") if isinstance(d2, dict) else ""
        st, L2 = a.get(f"/api/pdf/{sid}/lines/{src2}/0?ocr=1")
        txt = " ".join(l["text"] for l in L2.get("lines", [])) if isinstance(L2, dict) else ""
        check("OCR a scanned page (Tesseract)", st == 200 and "Report" in txt, str(L2)[:160])
    st, lib = a.get("/api/library")
    check("Recent files list", st == 200 and isinstance(lib, dict) and len(lib.get("items", [])) >= 1, str(lib)[:120])

    if WEB:
        print("Privacy between visitors")
        up, job, _ = first if first else (None, None, None)
        if up:
            check("other visitor can't see a conversion", b.get(f"/api/jobs/{job}")[0] == 404)
            check("other visitor can't download a file", b.get(f"/api/download/{job}", raw=True)[0] == 404)
            check("other visitor can't reuse an upload", b.post("/api/convert", {"id": up["id"], "target": "pdf"})[0] == 404)
        check("other visitor can't open an editing session", b.get(f"/api/pdf/{sid}/render/{src}/0", raw=True)[0] in (400, 404))
        st, libb = b.get("/api/library")
        check("other visitor's Recent is empty", st == 200 and libb.get("items") == [], str(libb)[:120])
        if isinstance(lib, dict) and lib.get("items"):
            st, sb = b.post("/api/pdf/session")
            lid = lib["items"][0]["id"]
            check("other visitor can't open your Recent file", b.post(f"/api/pdf/{sb['sid']}/add-from", {"library": lid})[0] == 404)
            check("other visitor can't see your Recent thumbnail", b.get(f"/api/library/{lid}/thumb", raw=True)[0] == 404)
        lim = h.get("limits", {}).get("upload_mb", 0)
        if lim and lim <= 8:
            st, _ = a.upload("/api/upload", "big.pdf", b"0" * (lim * 1024 * 1024 + 1024))
            check("oversized upload is refused", st == 413, st)

    # tidy up: remove the test document from Recent
    test_lib = d.get("lib") if isinstance(d, dict) else None
    if test_lib:
        a.req("DELETE", f"/api/library/{test_lib}")

    print()
    if FAILS:
        print(f"FAILED: {len(FAILS)} check(s): " + ", ".join(FAILS))
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
