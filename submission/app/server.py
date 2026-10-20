"""Local web UI for the knowledge system.

    python submission/app/server.py                 # then open http://127.0.0.1:8765
    python submission/app/server.py --port 9000 --snapshots path/to/snapshots

Standard library only. It binds to 127.0.0.1 by default and makes no outbound network calls.
The answers come from the same build() object that the scorer uses."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import zipfile
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
SUBMISSION = HERE.parent
sys.path.insert(0, str(SUBMISSION))

import solution  # noqa: E402
from kb import persist, working_memory  # noqa: E402
from kb.answer import MAX_QUESTION, clean_question, parse_as_of  # noqa: E402
from kb.contract_types import Page, Snapshot  # noqa: E402
from kb.text import month_end  # noqa: E402

STATIC_DIR = HERE / "static"
CACHE_FILE = HERE / ".cache" / "knowledge.json.gz"     # git-ignored; rebuilt when snapshots or code change
STATIC = {                                   # fixed list: no file outside it can be served
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}
MAX_BODY = 4096                              # bytes for one question request
RATE_LIMIT = (30, 10.0)                      # at most 30 requests in 10 seconds per client
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                               "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
                               "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


# ---- snapshot loading ------------------------------------------------------------------------
def _inside(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def unpack_zips(root: Path) -> None:
    """Unpack snapshots_zip/*.zip next to the snapshot folder, refusing any path that leaves the folder."""
    zip_dir = root.parent / "snapshots_zip"
    if not zip_dir.is_dir():
        return
    root.mkdir(parents=True, exist_ok=True)
    for z in sorted(zip_dir.glob("*.zip")):
        if (root / z.stem / "manifest.json").exists():
            continue
        with zipfile.ZipFile(z) as zf:
            for name in zf.namelist():
                if not _inside(root, root / name):
                    raise SystemExit(f"unsafe path in {z.name}: {name}")
            zf.extractall(root)


def load_snapshots(root: Path) -> list:
    unpack_zips(root)
    snaps = []
    for d in sorted(p for p in root.iterdir() if p.is_dir() and (p / "manifest.json").exists()):
        manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        pages = []
        for p in manifest.get("pages", []):
            f = d / str(p.get("file", ""))
            if not _inside(d, f) or not f.is_file():
                continue                     # a manifest entry may not point outside its snapshot folder
            pages.append(Page(url=p.get("url", ""), title=p.get("title", ""),
                              markdown=f.read_text(encoding="utf-8", errors="replace"),
                              fetched_at=p.get("fetched_at", manifest.get("taken_on", ""))))
        snaps.append(Snapshot(manifest["snapshot_id"], manifest["taken_on"], pages))
    snaps.sort(key=lambda s: s.taken_on)
    return snaps


# ---- application state -------------------------------------------------------------------------
class State:
    def __init__(self, use_cache: bool = True):
        self.ks = solution.build()
        self.ready = False
        self.error = None
        self.step = "Starting"
        self.done = 0
        self.total = 0
        self.started = time.time()
        self.load_seconds = None
        self.conflicts = []
        self.use_cache = use_cache
        self.from_cache = False

    def load(self, root: Path) -> None:
        try:
            self.step = "Reading snapshot files"
            snaps = load_snapshots(root)
            if not snaps:
                raise RuntimeError(f"no snapshots found in {root}")
            self.total = len(snaps)
            fp = persist.fingerprint(snaps)
            if self.use_cache:
                self.step = "Loading saved knowledge"
                self.from_cache = persist.load(self.ks.coordinator, CACHE_FILE, fp)
            if not self.from_cache:
                for s in snaps:
                    self.step = f"Reading {s.snapshot_id} ({len(s.pages)} pages)"
                    self.ks.ingest(s)
                    self.done += 1
            self.done = self.total
            self.step = "Comparing sources"
            self.conflicts = self.ks.conflicts()
            self.load_seconds = round(time.time() - self.started, 1)
            self.ready = True
            self.step = "Ready"
            if self.use_cache and not self.from_cache:
                # saved after the app is ready, so the first start is not slower; the next start reads it
                try:
                    persist.save(self.ks.coordinator, CACHE_FILE, fp)
                    print(f"[server] knowledge saved to {CACHE_FILE.name} for a fast next start", file=sys.stderr)
                except OSError as exc:
                    print(f"[server] could not save the cache: {exc}", file=sys.stderr)
        except Exception as exc:             # shown in the UI; the details stay in the server log
            self.error = "The snapshots could not be loaded. See the server window for details."
            print(f"[server] load failed: {type(exc).__name__}: {exc}", file=sys.stderr)

    def related_conflicts(self, ans) -> list:
        """Other values the website states for a fact in this answer: same page set, value shown in the answer."""
        urls = {s.url for s in ans.sources}
        text = ans.text or ""
        out = []
        for c in self.conflicts:
            if not urls & {s.url for s in c.sources}:
                continue
            if not any(v and v in text for v in c.values):
                continue
            out.append({"topic": c.topic, "values": list(c.values), "resolution": c.resolution, "reason": c.reason,
                        "sources": [source_dict(s) for s in c.sources][:4]})
            if len(out) >= 4:
                break
        return out

    def status(self) -> dict:
        co = self.ks.coordinator
        st = co.store
        return {
            "ready": self.ready, "error": self.error, "step": self.step, "done": self.done, "total": self.total,
            "load_seconds": self.load_seconds,
            "snapshots": co.stats if self.ready else [],
            "first_date": st.snaps[0].date if self.ready and st.snaps else None,
            "latest_date": st.snaps[-1].date if self.ready and st.snaps else None,
            "chunks": len(st.chunks) if self.ready else 0,
            "facts": sum(len(c.facts) for c in st.chunks) if self.ready else 0,
            "conflicts": len(self.conflicts),
            "from_cache": self.from_cache,
            "max_question": MAX_QUESTION,
        }


STATE: State | None = None
ALLOWED_HOSTS: set = set()
_hits: dict = defaultdict(deque)
_hits_lock = threading.Lock()


def allowed(client: str) -> bool:
    limit, window = RATE_LIMIT
    now = time.monotonic()
    with _hits_lock:
        q = _hits[client]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def source_dict(s) -> dict:
    url = str(getattr(s, "url", "") or "")
    return {"url": url if url.startswith(("http://", "https://")) else "",
            "snapshot_id": getattr(s, "snapshot_id", None), "quote": getattr(s, "quote", None)}


# ---- HTTP handler -------------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "kb-ui"
    sys_version = ""
    timeout = 15

    def log_message(self, fmt, *args):     # path only: question text is never written to the log
        sys.stderr.write(f"[server] {self.address_string()} {self.command} {urlparse(self.path).path} "
                         f"{args[1] if len(args) > 1 else ''}\n")

    def _send(self, code: int, body: bytes, ctype: str, cache: bool = False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache" if cache else "no-store")
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        """Blocks DNS-rebinding: the Host header must name this server."""
        host = (self.headers.get("Host") or "").lower()
        return not ALLOWED_HOSTS or host in ALLOWED_HOSTS

    def _origin_ok(self) -> bool:
        """Blocks cross-site posts: a browser that sends Origin must send ours."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        o = urlparse(origin)
        return o.scheme == "http" and (o.netloc or "").lower() in ALLOWED_HOSTS

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._host_ok():
            return self._json(421, {"error": "Wrong host."})
        path = urlparse(self.path).path
        if path in STATIC:
            name, ctype = STATIC[path]
            try:
                body = (STATIC_DIR / name).read_bytes()
            except OSError:
                return self._json(404, {"error": "Not found."})
            return self._send(200, body, ctype, cache=True)
        if path == "/api/status":
            return self._json(200, STATE.status())
        return self._json(404, {"error": "Not found."})

    def do_POST(self):
        if not self._host_ok() or not self._origin_ok():
            return self._json(403, {"error": "Request refused."})
        if urlparse(self.path).path != "/api/ask":
            return self._json(404, {"error": "Not found."})
        if not allowed(self.client_address[0]):
            return self._json(429, {"error": "Too many questions. Please wait a few seconds."})
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._json(415, {"error": "Send JSON."})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_BODY:
            return self._json(413, {"error": "The request is empty or too large."})
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json(400, {"error": "The request is not valid JSON."})
        if not isinstance(data, dict):
            return self._json(400, {"error": "The request is not valid JSON."})
        if not STATE.ready:
            return self._json(503, {"error": "The snapshots are still loading."})
        question = clean_question(data.get("question"))
        if not question:
            return self._json(400, {"error": "Type a question first."})
        as_of_raw = data.get("as_of")
        as_of_raw = as_of_raw if isinstance(as_of_raw, str) else None
        as_of, bad = parse_as_of(as_of_raw)
        previous = data.get("previous")
        previous = previous if isinstance(previous, str) else None
        # working memory: a short follow-up ("what about 72 months?") is joined to the previous question
        resolved, used_memory = working_memory.resolve(question, previous)
        t = time.perf_counter()
        ans = STATE.ks.answer(resolved, as_of if not bad else as_of_raw)
        ms = round((time.perf_counter() - t) * 1000, 1)
        st = STATE.ks.coordinator.store
        si = st.snapshot_for(as_of or month_end(resolved)) if not bad else None
        used = st.snaps[si].date if si is not None and si >= 0 else None
        return self._json(200, {
            "question": question, "resolved": resolved if used_memory else None, "as_of": as_of,
            "text": ans.text, "confident": bool(ans.confident), "conflict_note": ans.conflict_note,
            "sources": [source_dict(s) for s in ans.sources][:8],
            "other_values": STATE.related_conflicts(ans) if ans.confident else [],
            "snapshot_date": used, "latest_date": st.snaps[-1].date if st.snaps else None, "elapsed_ms": ms,
        })

    def _refuse(self):
        self._json(405, {"error": "Method not allowed."})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_CONNECT = _refuse


def main() -> None:
    global STATE, ALLOWED_HOSTS
    ap = argparse.ArgumentParser(description="Local web UI for the credit-union knowledge system.")
    ap.add_argument("--host", default="127.0.0.1", help="interface to bind (default 127.0.0.1, local only)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--snapshots", type=Path, default=SUBMISSION.parent / "snapshots",
                    help="folder with the snapshot folders (unpacked from ../snapshots_zip if needed)")
    ap.add_argument("--no-cache", action="store_true", help="always rebuild the knowledge; do not read or write "
                    "the saved copy in submission/app/.cache")
    args = ap.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("[server] WARNING: binding to a non-local address exposes the app to your network.", file=sys.stderr)
    names = {args.host, "127.0.0.1", "localhost"} if args.host in ("127.0.0.1", "localhost") else {args.host}
    ALLOWED_HOSTS = {f"{h}:{args.port}" for h in names} | ({"[::1]:%d" % args.port} if args.host == "::1" else set())
    STATE = State(use_cache=not args.no_cache)
    threading.Thread(target=STATE.load, args=(args.snapshots,), daemon=True).start()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    print(f"[server] open http://{'localhost' if args.host in ('127.0.0.1', 'localhost') else args.host}:{args.port}"
          f"  (loading snapshots from {args.snapshots})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
