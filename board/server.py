#!/usr/bin/env python3
"""ThreadTrace gateway server for the Arduino UNO Q (Linux side).

Standard library only - nothing to pip install on the board.

  python3 server.py                 # serves on 0.0.0.0:8000
  GEMINI_API_KEY=... python3 server.py   # enables reading the scale photo with Gemini

Routes
  GET  /                 big-screen dashboard (static/index.html)
  GET  /s/<asset_id>     garment QR target: records a scan, shows a phone confirmation page
  GET  /weigh            phone page: photograph the scale, Gemini reads the kg
  GET  /cards            printable QR cards for the demo garments
  POST /api/scan         {"asset_id": "..."}
  POST /api/weigh        {"image": "<base64 jpeg>", "kg": optional manual value}
  GET  /events           all field events (JSON)
  GET  /stream           Server-Sent Events: one message per new field event
  GET  /led              latest text for the LED matrix (polled by the App Lab app)
"""
import base64
import json
import mimetypes
import os
import platform
import queue
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
PHOTOS = os.path.join(STATIC, "photos")
DB = os.path.join(ROOT, "threadtrace.db")
PORT = int(os.environ.get("PORT", "8000"))
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GATEWAY = os.environ.get("GATEWAY_NAME", "edge:" + platform.node())

os.makedirs(PHOTOS, exist_ok=True)
with open(os.path.join(STATIC, "data.json"), encoding="utf-8") as fh:
    DATA = json.load(fh)

_lock = threading.Lock()
_clients = []          # one queue.Queue per open /stream connection
_led = {"text": "THREADTRACE", "seq": 0, "kind": 0, "a": 0, "b": 0}


def db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, type TEXT, asset_id TEXT, site_id TEXT,
        kg REAL, photo TEXT, reader TEXT, source TEXT, data_status TEXT)""")
    return con


def row_to_event(r):
    keys = ["id", "ts", "type", "asset_id", "site_id", "kg", "photo", "reader", "source", "data_status"]
    return {k: v for k, v in zip(keys, r) if v is not None}


def all_events():
    with _lock, db() as con:
        return [row_to_event(r) for r in con.execute("SELECT * FROM events ORDER BY id")]


def add_event(ev):
    ev.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
    ev.setdefault("source", GATEWAY)
    ev.setdefault("data_status", "FIELD_SCAN_DEMO")
    with _lock, db() as con:
        cur = con.execute("INSERT INTO events(ts,type,asset_id,site_id,kg,photo,reader,source,data_status) VALUES(?,?,?,?,?,?,?,?,?)",
                          (ev["ts"], ev["type"], ev.get("asset_id"), ev.get("site_id"), ev.get("kg"), ev.get("photo"),
                           ev.get("reader"), ev["source"], ev["data_status"]))
        ev["id"] = cur.lastrowid
    msg = json.dumps(ev)
    for q in list(_clients):
        q.put(msg)
    set_led(ev)
    print(f"[event] {msg}", flush=True)
    return ev


def set_led(ev):
    """Latest event for the LED app: kind 1 = scan (a = cycles), kind 2 = weigh (a = kg x 10, b = litres saved)."""
    if ev["type"] == "scan":
        p = DATA["passports"].get(ev["asset_id"], {"cy": []})
        _led.update(kind=1, a=len(p["cy"]), b=0, text=f"+1 {ev['asset_id']}")
    else:
        lf = DATA["laundries"]["L001"]
        b = DATA["baselines"]["B_LOCAL"]
        saved = ev["kg"] * (b["water_l_per_kg"] - lf["water_l_per_kg"])
        _led.update(kind=2, a=int(round(ev["kg"] * 10)), b=int(round(saved)), text=f"{ev['kg']:.1f}KG -{saved:.0f}L")
    _led["seq"] += 1


def passport_summary(aid):
    p = DATA["passports"].get(aid)
    if not p:
        return None
    prod = DATA["products"][p["p"]]
    cust = DATA["customers"][p["c"]]
    site = next((s for s in cust["sites"] if s["id"] == p["s"]), {"name": p["s"]})
    return {"product": prod["name"], "site": site["name"], "customer": cust["name"], "cycles": len(p["cy"]), "site_id": p["s"]}


_model = {"name": GEMINI_MODEL, "candidates": []}


def list_gemini_models():
    """Ask the API which models this key can use; best Flash models first."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_KEY}&pageSize=200"
    with urllib.request.urlopen(url, timeout=15) as r:
        models = json.load(r).get("models", [])
    names = [m["name"].split("/", 1)[-1] for m in models
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    def score(n):
        bad = any(w in n for w in ("tts", "audio", "image-gen", "live", "embedding", "thinking", "exp"))
        return (("flash" in n) and not bad, "preview" not in n, "latest" in n, n)
    names = [n for n in sorted(names, key=score, reverse=True)
             if "flash" in n and not any(w in n for w in ("tts", "audio", "live", "embedding"))] or names
    print(f"[gemini] candidates: {names[:8]}", flush=True)
    return names


def _gemini_call(model, body):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def read_scale_with_gemini(jpeg_b64):
    """Ask Gemini to read the number on the scale display. Returns (kg or None, note).
    Tries the current model first, then other available Flash models on 404 / 429 / 5xx."""
    if not GEMINI_KEY:
        return None, "GEMINI_API_KEY not set"
    prompt = ("This photo shows a weighing scale display. Read the weight shown. "
              'Reply only with JSON: {"kg": <number or null>, "unit_seen": "<kg|lb|g|unknown>"}. '
              "Convert to kilograms. If no readable scale is visible, use null.")
    body = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": jpeg_b64}}]}],
            "generationConfig": {"response_mime_type": "application/json", "temperature": 0}}
    tried, last_err = [], "no model tried"
    queue = [_model["name"]] + [m for m in _model["candidates"] if m != _model["name"]]
    while queue and len(tried) < 5:
        model = queue.pop(0)
        if model in tried:
            continue
        tried.append(model)
        try:
            out = _gemini_call(model, body)
            text = out["candidates"][0]["content"]["parts"][0]["text"]
            m = re.search(r"\{.*\}", text, re.S)
            val = json.loads(m.group(0) if m else text).get("kg")
            _model["name"] = model
            return (float(val) if val is not None else None), "gemini"
        except urllib.error.HTTPError as e:
            last_err = f"{e.code} ({model}): {e.read().decode(errors='ignore')[:120]}"
            print(f"[gemini] {last_err}", flush=True)
            if e.code in (400, 404, 429, 500, 503) and not _model["candidates"]:
                try:
                    _model["candidates"] = list_gemini_models()
                except Exception as le:
                    last_err += f"; list models failed: {le}"
            if e.code in (400, 404, 429, 500, 503):
                queue += [c for c in _model["candidates"] if c not in tried and c not in queue]
                time.sleep(0.4)
                continue
            break
        except Exception as e:  # network, parsing
            last_err = f"({model}): {e}"
            break
    return None, f"gemini error {last_err}"


PHONE_CSS = """<meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{margin:0;background:#070b10;color:#e9eff6;font-family:-apple-system,system-ui,sans-serif;padding:28px 22px}
.ok{width:74px;height:74px;border-radius:50%;background:#22c55e;display:grid;place-items:center;font-size:40px;margin:10px 0 18px;box-shadow:0 0 40px rgba(34,197,94,.5)}
h1{font-size:26px;margin:0 0 6px}.m{color:#8696a8;font-size:15px;line-height:1.5}.b{display:block;width:100%;padding:16px;border-radius:14px;border:0;
background:#38bdf8;color:#04121b;font-size:17px;font-weight:700;margin-top:18px;text-align:center;text-decoration:none;box-sizing:border-box}
.g{background:#121a24;color:#e9eff6;border:1px solid #1d2834}input{width:100%;padding:14px;border-radius:12px;border:1px solid #1d2834;background:#0e141c;color:#e9eff6;font-size:17px;box-sizing:border-box}
img{max-width:100%;border-radius:14px;margin-top:14px}.tag{font-family:monospace;font-size:11px;color:#4ade80;border:1px solid #166534;border-radius:5px;padding:2px 6px}
</style>"""


def page_scan(aid, info):
    if not info:
        return f"<!doctype html><title>ThreadTrace</title>{PHONE_CSS}<h1>Unknown garment</h1><p class=m>{aid} is not in the data pack.</p>"
    return f"""<!doctype html><title>ThreadTrace scan</title>{PHONE_CSS}
<div class=ok>✓</div><h1>{info['product']}</h1>
<p class=m>{aid} · {info['site']}<br>{info['customer']} · {info['cycles']} service cycles in 2025</p>
<p><span class=tag>FIELD SCAN (DEMO)</span></p>
<p class=m>Sent to the review screen. Look up!</p>
<a class="b g" href="/weigh">Weigh a return bag →</a>"""


PAGE_WEIGH = f"""<!doctype html><title>ThreadTrace weigh-in</title>{PHONE_CSS}
<h1>Weigh-in</h1><p class=m>Photograph the scale display. The gateway reads the weight{' with Gemini' if True else ''} and the review screen shows the water saved.</p>
<label class=b id=lab>📷 Take photo of scale<input id=cam type=file accept="image/*" capture="environment" hidden></label>
<p class=m style="margin-top:22px">Or type the weight:</p><input id=kg type=number step=0.1 inputmode=decimal placeholder="kg">
<button class="b g" id=send>Send weight</button>
<img id=prev hidden><p class=m id=st></p><button class="b g" id=retry hidden>↻ Try reading this photo again</button>
<script>
const st=document.getElementById('st');
function shrink(file){{return new Promise(res=>{{const img=new Image();img.onload=()=>{{const k=Math.min(1,1024/Math.max(img.width,img.height));
 const c=document.createElement('canvas');c.width=img.width*k;c.height=img.height*k;c.getContext('2d').drawImage(img,0,0,c.width,c.height);
 res(c.toDataURL('image/jpeg',.82));}};img.src=URL.createObjectURL(file);}});}}
async function send(body){{st.textContent='Sending…';const r=await fetch('/api/weigh',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
 const j=await r.json();st.innerHTML=j.ok?`✓ ${{j.event.kg}} kg sent to the screen (${{j.note}})`:`Could not read the weight (${{j.note}}). Tap "Try again" or type the weight above.`;
 document.getElementById('retry').hidden=j.ok||!body.image;}}
let lastImg=null;document.getElementById('retry').onclick=()=>{{if(lastImg){{st.textContent='Trying again…';send({{image:lastImg}});}}}};
document.getElementById('cam').onchange=async e=>{{const f=e.target.files[0];if(!f)return;const d=await shrink(f);const p=document.getElementById('prev');p.src=d;p.hidden=false;
 st.textContent='Reading the scale…';lastImg=d.split(',')[1];send({{image:lastImg,kg:parseFloat(document.getElementById('kg').value)||null}});}};
document.getElementById('send').onclick=()=>{{const v=parseFloat(document.getElementById('kg').value);if(!v){{st.textContent='Enter a weight first.';return;}}send({{kg:v}});}};
</script>"""


def page_cards(base):
    ids = DATA["demo_assets"]
    cards = []
    for aid in ids:
        info = passport_summary(aid)
        cards.append(f"""<div class=card><div class=qr data-u="{base}/s/{aid}"></div><b>{info['product']}</b>
<span>{aid} · {info['site']}</span><span class=u>{base}/s/{aid}</span></div>""")
    cards.append(f"""<div class=card><div class=qr data-u="{base}/weigh"></div><b>Weigh-in station</b><span>Photograph the scale</span><span class=u>{base}/weigh</span></div>""")
    return f"""<!doctype html><meta charset=utf-8><title>ThreadTrace QR cards</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<style>body{{font-family:system-ui,sans-serif;margin:20px}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}}
.card{{border:1.5px dashed #999;border-radius:12px;padding:16px;display:flex;flex-direction:column;align-items:center;gap:6px;text-align:center;font-size:13px}}
.card b{{font-size:16px}}.u{{font-family:monospace;font-size:9px;color:#666;word-break:break-all}}.qr{{margin-bottom:8px}}
@media print{{.no{{display:none}}}}</style>
<p class=no>QR codes point to <b>{base}</b>. Print this page, or open it on a second screen. Change with <code>?base=https://your-host</code>.</p>
<div class=grid>{''.join(cards)}</div>
<script>document.querySelectorAll('.qr').forEach(n=>new QRCode(n,{{text:n.dataset.u,width:170,height:170}}));</script>"""


class H(BaseHTTPRequestHandler):
    server_version = "ThreadTrace/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj), "application/json")

    def _base(self):
        q = parse_qs(urlparse(self.path).query)
        if q.get("base"):
            return q["base"][0].rstrip("/")
        proto = self.headers.get("X-Forwarded-Proto", "http")
        return f"{proto}://{self.headers.get('Host', f'localhost:{PORT}')}"

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._static("index.html")
        m = re.fullmatch(r"/s/([A-Za-z0-9_-]+)", path)
        if m:
            aid = m.group(1)
            info = passport_summary(aid)
            if info:
                add_event({"type": "scan", "asset_id": aid, "site_id": info["site_id"], "reader": "qr"})
            return self._send(200 if info else 404, page_scan(aid, info))
        if path == "/weigh":
            return self._send(200, PAGE_WEIGH)
        if path == "/scale":
            return self._static("scale.html")
        if path == "/cards":
            return self._send(200, page_cards(self._base()))
        if path == "/events":
            return self._json(all_events())
        if path == "/led":
            return self._json(_led)
        if path == "/health":
            return self._json({"ok": True, "gateway": GATEWAY, "gemini": bool(GEMINI_KEY), "model": _model["name"]})
        if path == "/stream":
            return self._stream()
        return self._static(path.lstrip("/"))

    def do_POST(self):
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n > 15_000_000:
            return self._json({"ok": False, "note": "too large"}, 413)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json({"ok": False, "note": "bad json"}, 400)
        if path == "/api/scan":
            aid = str(body.get("asset_id", ""))
            info = passport_summary(aid)
            if not info:
                return self._json({"ok": False, "note": "unknown asset"}, 404)
            ev = add_event({"type": "scan", "asset_id": aid, "site_id": info["site_id"], "reader": body.get("source", "api")})
            return self._json({"ok": True, "event": ev})
        if path == "/api/weigh":
            photo, kg, note = None, None, "manual"
            if body.get("image"):
                raw = base64.b64decode(body["image"])
                name = f"weigh_{int(time.time() * 1000)}.jpg"
                with open(os.path.join(PHOTOS, name), "wb") as fh:
                    fh.write(raw)
                photo = f"photos/{name}"
                kg, note = read_scale_with_gemini(body["image"])
            if kg is None and body.get("kg"):
                kg, note = float(body["kg"]), (note + "; manual value used" if photo else "manual")
            if kg is None or not (0 < kg < 500):
                return self._json({"ok": False, "note": note})
            ev = add_event({"type": "weigh", "kg": round(kg, 1), "photo": photo,
                            "reader": "gemini" if note == "gemini" else "manual"})
            return self._json({"ok": True, "event": ev, "note": note})
        return self._json({"ok": False, "note": "not found"}, 404)

    def _static(self, rel):
        full = os.path.normpath(os.path.join(STATIC, rel))
        if not full.startswith(STATIC) or not os.path.isfile(full):
            return self._send(404, "not found", "text/plain")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            self._send(200, fh.read(), ctype)

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        q = queue.Queue()
        _clients.append(q)
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(f"data: {msg}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            _clients.remove(q)


if __name__ == "__main__":
    db().close()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    srv.daemon_threads = True
    print(f"ThreadTrace gateway on http://0.0.0.0:{PORT}  (gemini: {'on' if GEMINI_KEY else 'off'}, model {GEMINI_MODEL})", flush=True)
    srv.serve_forever()
