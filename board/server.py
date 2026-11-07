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


COLS = ["id", "ts", "type", "asset_id", "site_id", "kg", "photo", "reader", "source", "data_status",
        "lot_id", "qty", "location", "detail"]


def db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, type TEXT, asset_id TEXT, site_id TEXT,
        kg REAL, photo TEXT, reader TEXT, source TEXT, data_status TEXT)""")
    for col, typ in (("lot_id", "TEXT"), ("qty", "INTEGER"), ("location", "TEXT"), ("detail", "TEXT")):
        try:
            con.execute(f"ALTER TABLE events ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass  # column already there
    return con


def row_to_event(r):
    return {k: v for k, v in zip(COLS, r) if v is not None}


def all_events():
    with _lock, db() as con:
        return [row_to_event(r) for r in con.execute(f"SELECT {','.join(COLS)} FROM events ORDER BY id")]


def add_event(ev):
    ev.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
    ev.setdefault("source", GATEWAY)
    ev.setdefault("data_status", "FIELD_SCAN_DEMO")
    with _lock, db() as con:
        cols = COLS[1:]
        cur = con.execute(f"INSERT INTO events({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                          tuple(ev.get(c) for c in cols))
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
    elif ev["type"] in ("checkin", "repair", "writeoff"):
        _led.update(kind=1, a=max(1, min(12, int(ev.get("qty") or 1))), b=0, text=f"{ev['type']} {ev.get('lot_id')}")
    else:
        lf = DATA["laundries"]["L001"]
        b = DATA["baselines"]["B_LOCAL"]
        saved = ev["kg"] * (b["water_l_per_kg"] - lf["water_l_per_kg"])
        _led.update(kind=2, a=int(round(ev["kg"] * 10)), b=int(round(saved)), text=f"{ev['kg']:.1f}KG -{saved:.0f}L")
    _led["seq"] += 1


def weigh_impact(kg, cust="CUST001"):
    c = DATA["customers"][cust]
    lid = c["sites"][0]["laundry"]
    lf, b = DATA["laundries"][lid], DATA["baselines"]["B_LOCAL"]
    prods = {p["p"] for p in DATA["passports"].values() if p["c"] == cust}
    avg = sum(DATA["products"][p]["kg"] for p in prods) / max(len(prods), 1)
    return {"water_l": round(kg * (b["water_l_per_kg"] - lf["water_l_per_kg"])),
            "co2_kg": round(kg * (b["kgco2e_per_kg"] - lf["kgco2e_per_kg"]), 1),
            "pieces": round(kg / avg), "baseline": b["name"].lower()}


def passport_summary(aid):
    p = DATA["passports"].get(aid)
    if not p:
        return None
    prod = DATA["products"][p["p"]]
    cust = DATA["customers"][p["c"]]
    site = next((s for s in cust["sites"] if s["id"] == p["s"]), {"name": p["s"]})
    return {"product": prod["name"], "site": site["name"], "customer": cust["name"], "customer_id": p["c"],
            "cycles": len(p["cy"]), "site_id": p["s"], "lot": p.get("k")}


REPAIR_TYPES = ["ZIPPER", "BUTTON", "DARNING / PATCHING", "OTHER SEAM STITCHING", "OTHER ACCESSORIES"]
WRITEOFF_REASONS = ["Worn out", "Damaged beyond repair", "Lost"]


def lot_summary(lid):
    l = DATA.get("lots", {}).get(lid)
    if not l:
        return None
    prod, cust = DATA["products"][l["p"]], DATA["customers"][l["c"]]
    site = next((s for s in cust["sites"] if s["id"] == l["s"]), {"name": l["s"]})
    return {"id": lid, "product": prod["name"], "group": prod["group"], "qty": l["n"], "produced": l["prod"],
            "delivered": l["del"], "customer": cust["name"], "customer_id": l["c"], "site": site["name"],
            "site_id": l["s"], "cost_eur": prod["cost_eur"], "footprint": prod["footprint_kgco2e"], "kg": prod["kg"]}


def lot_locations(cid):
    c = DATA["customers"][cid]
    locs = [(s["id"], s["name"]) for s in c["sites"]]
    locs += [(lid, "Lindström laundry · " + DATA["laundries"][lid]["name"]) for lid in sorted({s["laundry"] for s in c["sites"]})]
    return locs


def location_name(cid, loc):
    return dict(lot_locations(cid)).get(loc, loc or "")


def lot_impact(info, action, qty):
    if action in ("repair", "writeoff"):
        return {"eur": round(qty * info["cost_eur"]), "co2_kg": round(qty * info["footprint"], 1),
                "label": "replacement avoided" if action == "repair" else "replacement value"}
    return {}


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
[hidden]{display:none!important}
.res{margin-top:18px;border-radius:18px;padding:22px;background:linear-gradient(160deg,#0f2233,#0b141d);border:1px solid #1e4a66;text-align:center}
.res .kg{font-size:56px;font-weight:800;letter-spacing:-1px}.res .arrow{color:#8696a8;margin:6px 0}.res .w{font-size:40px;font-weight:800;color:#38bdf8;text-shadow:0 0 24px rgba(56,189,248,.45)}
.res .s{color:#8696a8;font-size:15px;margin-top:8px}
body{margin:0;background:#070b10;color:#e9eff6;font-family:-apple-system,system-ui,sans-serif;padding:28px 22px}
.ok{width:74px;height:74px;border-radius:50%;background:#22c55e;display:grid;place-items:center;font-size:40px;margin:10px 0 18px;box-shadow:0 0 40px rgba(34,197,94,.5)}
h1{font-size:26px;margin:0 0 6px}.m{color:#8696a8;font-size:15px;line-height:1.5}.b{display:block;width:100%;padding:16px;border-radius:14px;border:0;
background:#38bdf8;color:#04121b;font-size:17px;font-weight:700;margin-top:18px;text-align:center;text-decoration:none;box-sizing:border-box}
.g{background:#121a24;color:#e9eff6;border:1px solid #1d2834}input{width:100%;padding:14px;border-radius:12px;border:1px solid #1d2834;background:#0e141c;color:#e9eff6;font-size:17px;box-sizing:border-box}
img{max-width:100%;border-radius:14px;margin-top:14px}.tag{font-family:monospace;font-size:11px;color:#4ade80;border:1px solid #166534;border-radius:5px;padding:2px 6px}
.nav{display:flex;gap:8px;margin:-12px 0 20px}.nav a{flex:1;text-align:center;padding:11px 6px;border-radius:12px;background:#0e141c;border:1px solid #1d2834;color:#e9eff6;text-decoration:none;font-size:15px;font-weight:600}
.nav a.sc{background:#0b2a3c;border-color:#1e4a66;color:#7dd3fc}
.money{font-weight:800;background:linear-gradient(180deg,#fff3c4,#f6c343 55%,#e09b1c);-webkit-background-clip:text;background-clip:text;color:transparent!important;text-shadow:none!important}
</style>
<nav class=nav><a href="#" onclick="if(history.length>1){history.back()}else{location.href='/m'};return false">← Back</a><a href="/m">🏠 Home</a><a class=sc href="/m?scan=1">📷 Scan</a></nav>"""


def page_scan(aid, info):
    if not info:
        return f"<!doctype html><title>ThreadTrace</title>{PHONE_CSS}<h1>Unknown garment</h1><p class=m>{aid} is not in the data pack.</p>"
    return f"""<!doctype html><title>ThreadTrace scan</title>{PHONE_CSS}
<div class=ok>✓</div><h1>{info['product']}</h1>
<p class=m>{aid} · {info['site']}<br>{info['customer']} · {info['cycles']} service cycles in 2025</p>
<p><span class=tag>FIELD SCAN (DEMO)</span></p>
<p class=m>📦 Part of package <b>{info.get('lot') or '–'}</b>. Sent to the review screen.</p>
<a class=b href="/?attract=0&cust={info['customer_id']}&play={aid}">▶ See this garment's story</a>
<a class="b g" href="/p/{info.get('lot')}?act=repair&g={aid}">🔧 Report a repair for this garment</a>
<a class="b g" href="/p/{info.get('lot')}">📦 Open its package</a>
<a class="b g" href="/?attract=0&cust={info['customer_id']}">📊 Open the {info['customer']} review</a>
<a class="b g" href="/?attract=0&cust={info['customer_id']}&report=1">📄 Read the annual report</a>
<a class="b g" href="/weigh">⚖ Weigh a return bag</a>"""


PAGE_WEIGH = f"""<!doctype html><title>ThreadTrace weigh-in</title>{PHONE_CSS}
<h1>Weigh-in</h1><p class=m id=lotline hidden></p><p class=m>Photograph the scale display. The gateway reads the weight{' with Gemini' if True else ''} and the review screen shows the water saved.</p>
<label class=b id=lab>📷 Take photo of scale<input id=cam type=file accept="image/*" capture="environment" hidden></label>
<p class=m style="margin-top:22px">Or type the weight:</p><input id=kg type=number step=0.1 inputmode=decimal placeholder="kg">
<button class="b g" id=send>Send weight</button>
<div class=res id=res hidden><div class=kg id=rkg></div><div class=arrow>washed by Lindström instead of a local laundry →</div><div class=w id=rw></div><div class=s id=rs></div><div class=s style="color:#4ade80;margin-top:14px">▲ Also shown on the big screen</div></div>
<div id=next hidden><a class=b href="/?attract=0&cust=CUST001">📊 Open the customer review</a><a class="b g" href="/?attract=0&cust=CUST001&report=1">📄 Read the annual report</a></div>
<img id=prev hidden><p class=m id=st></p><button class="b g" id=retry hidden>↻ Try reading this photo again</button>
<script>
const st=document.getElementById('st'), LOT=new URLSearchParams(location.search).get('lot');
if(LOT){{const l=document.getElementById('lotline');l.hidden=false;l.innerHTML='📦 Package <b>'+LOT+'</b>';}}
function shrink(file){{return new Promise(res=>{{const img=new Image();img.onload=()=>{{const k=Math.min(1,1024/Math.max(img.width,img.height));
 const c=document.createElement('canvas');c.width=img.width*k;c.height=img.height*k;c.getContext('2d').drawImage(img,0,0,c.width,c.height);
 res(c.toDataURL('image/jpeg',.82));}};img.src=URL.createObjectURL(file);}});}}
async function send(body){{if(LOT)body.lot_id=LOT;st.textContent='Sending…';const r=await fetch('/api/weigh',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
 const j=await r.json();st.innerHTML=j.ok?`✓ ${{j.note==='manual'?'Typed in':'Read by '+j.note}} and sent to the review screen.`:`Could not read the weight (${{j.note}}). Tap "Try again" or type the weight above.`;
 const res=document.getElementById('res');res.hidden=!j.ok;
 if(j.ok){{document.getElementById('rkg').textContent=j.event.kg+' kg';document.getElementById('rw').textContent='≈ '+j.impact.water_l+' L water saved';
  document.getElementById('rs').textContent='≈ '+j.impact.pieces+' garments · '+j.impact.co2_kg+' kg CO₂e avoided · estimate';
  document.getElementById('prev').hidden=true;document.getElementById('next').hidden=false;res.scrollIntoView({{behavior:'smooth'}});}}
 document.getElementById('retry').hidden=j.ok||!body.image;}}
let lastImg=null;document.getElementById('retry').onclick=()=>{{if(lastImg){{st.textContent='Trying again…';send({{image:lastImg}});}}}};
document.getElementById('cam').onchange=async e=>{{const f=e.target.files[0];if(!f)return;const d=await shrink(f);const p=document.getElementById('prev');p.src=d;p.hidden=false;
 st.textContent='Reading the scale…';lastImg=d.split(',')[1];send({{image:lastImg,kg:parseFloat(document.getElementById('kg').value)||null}});}};
document.getElementById('send').onclick=()=>{{const v=parseFloat(document.getElementById('kg').value);if(!v){{st.textContent='Enter a weight first.';return;}}send({{kg:v}});}};
</script>"""


def page_lot(lid, info, act, g):
    if not info:
        return f"<!doctype html><title>ThreadTrace</title>{PHONE_CSS}<h1>Unknown package</h1><p class=m>{lid} is not in the data.</p>"
    locs = "".join(f"<option value='{k}'{' selected' if k == info['site_id'] else ''}>{v}</option>" for k, v in lot_locations(info["customer_id"]))
    rtypes = "".join(f"<option>{t}</option>" for t in REPAIR_TYPES)
    reasons = "".join(f"<option>{t}</option>" for t in WRITEOFF_REASONS)
    hist = [e for e in all_events() if e.get("lot_id") == lid][-5:][::-1]
    label = {"checkin": "📍 Checked in", "repair": "🔧 Repaired", "writeoff": "❌ Written off", "weigh": "⚖ Weighed"}
    hrows = "".join(f"<li>{e['ts'][5:16].replace('T', ' ')} · {label.get(e['type'], e['type'])}"
                    f"{' ' + str(e.get('qty')) + ' pcs' if e.get('qty') else ''}{' ' + str(e.get('kg')) + ' kg' if e.get('kg') else ''}"
                    f" · {location_name(info['customer_id'], e.get('location') or e.get('site_id'))}</li>" for e in hist) or "<li>No field events yet</li>"
    return f"""<!doctype html><title>Package {lid}</title>{PHONE_CSS}
<style>.card{{background:#0e141c;border:1px solid #1d2834;border-radius:16px;padding:16px;margin-top:14px}}
.kv{{display:grid;grid-template-columns:auto 1fr;gap:6px 14px;font-size:15px}}.kv span:nth-child(odd){{color:#8696a8}}
select{{width:100%;padding:14px;border-radius:12px;border:1px solid #1d2834;background:#0e141c;color:#e9eff6;font-size:16px;box-sizing:border-box;margin-top:6px}}
.row{{display:flex;gap:10px}}.row>*{{flex:1}} ul{{margin:6px 0 0;padding-left:18px;color:#8696a8;font-size:14px;line-height:1.6}}
.form{{margin-top:10px}} .lbl{{color:#8696a8;font-size:14px;margin-top:12px;display:block}}</style>
<p class=m style="margin:0">📦 Package</p><h1 style="font-size:30px">{lid}</h1>
<div class=card><div class=kv>
<span>Style</span><b>{info['product']} ({info['group']})</b>
<span>Quantity</span><b>{info['qty']} garments</b>
<span>Customer</span><b>{info['customer']}</b>
<span>Assigned to</span><b>{info['site']}</b>
<span>Produced</span><b>{info['produced']} <span class=tag style="color:#f0abfc;border-color:#6b2a73">DEMO DATE</span></b>
<span>Delivered</span><b>{info['delivered']} <span class=tag style="color:#f0abfc;border-color:#6b2a73">DEMO DATE</span></b>
</div></div>
<label class=lbl for=loc>Where are you now?</label><select id=loc>{locs}</select>
<button class=b id=bIn>📍 Check in here</button>
<button class="b g" id=bRep>🔧 Report repair</button>
<div class=form id=fRep hidden><div class=row><div><span class=lbl>Garments</span><input id=rq type=number min=1 max={info['qty']} value=1 inputmode=numeric></div>
<div><span class=lbl>Repair</span><select id=rt>{rtypes}</select></div></div><button class=b id=sRep>Send repair</button></div>
<button class="b g" id=bOff>❌ Report write-off</button>
<div class=form id=fOff hidden><div class=row><div><span class=lbl>Garments</span><input id=oq type=number min=1 max={info['qty']} value=1 inputmode=numeric></div>
<div><span class=lbl>Reason</span><select id=or>{reasons}</select></div></div><button class=b id=sOff>Send write-off</button></div>
<a class="b g" href="/weigh?lot={lid}">⚖ Weigh this package</a>
<div class=res id=res hidden><div class=kg id=r1 style="font-size:34px"></div><div class=w id=r2 style="font-size:36px"></div><div class=s id=r3></div>
<div class=s style="color:#4ade80;margin-top:12px">▲ Also shown on the big screen</div></div>
<div class=card><b>Recent field events</b><ul id=hist>{hrows}</ul></div>
<a class="b g" href="/?attract=0&cust={info['customer_id']}">📊 Open the {info['customer']} review</a>
<a class="b g" href="/?attract=0&cust={info['customer_id']}&report=1">📄 Read the annual report</a>
<script>
const $=id=>document.getElementById(id), G={json.dumps(g or '')};
async function send(action, qty, detail){{
  const r=await fetch('/api/lot',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{lot_id:'{lid}',action,qty,detail,location:$('loc').value,asset_id:G||null}})}});
  const j=await r.json(); if(!j.ok){{alert(j.note);return;}}
  const res=$('res'); res.hidden=false;
  if(action==='checkin'){{ $('r1').textContent='📍 Checked in'; $('r2').textContent=$('loc').selectedOptions[0].text; $('r3').textContent='{info['qty']} × {info['product']} · '+j.event.ts.slice(0,16).replace('T',' '); }}
  else if(action==='repair'){{ $('r1').textContent='🔧 '+qty+' repaired'; $('r2').textContent='≈ €'+j.impact.eur+' saved'; $('r2').className='w money'; $('r3').textContent='replacement avoided · ≈ '+j.impact.co2_kg+' kg CO₂e avoided · estimate'; }}
  else {{ $('r1').textContent='❌ '+qty+' written off'; $('r2').textContent='≈ €'+j.impact.eur; $('r2').className='w'; $('r2').style.color='#f87171'; $('r3').textContent='replacement value · '+detail+' · estimate'; }}
  res.scrollIntoView({{behavior:'smooth'}});
}}
$('bIn').onclick=()=>send('checkin',{info['qty']},'');
$('bRep').onclick=()=>{{ $('fRep').hidden=!$('fRep').hidden; $('fOff').hidden=true; }};
$('bOff').onclick=()=>{{ $('fOff').hidden=!$('fOff').hidden; $('fRep').hidden=true; }};
$('sRep').onclick=()=>send('repair',Math.max(1,parseInt($('rq').value)||1),$('rt').value);
$('sOff').onclick=()=>send('writeoff',Math.max(1,parseInt($('oq').value)||1),$('or').value);
if({json.dumps(act or '')}==='repair') {{ $('fRep').hidden=false; }}
</script>"""


def page_home(cid, scan):
    cid = cid if cid in DATA["customers"] else next(iter(DATA["customers"]))
    custs = "".join(f"<option value='{k}'{' selected' if k == cid else ''}>{c['name']}</option>" for k, c in DATA["customers"].items())
    lots = [k for k in DATA.get("demo_lots", []) if DATA["lots"][k]["c"] == cid]
    lots += [k for k, l in DATA["lots"].items() if l["c"] == cid and k not in lots][:6 - len(lots)]
    lrows = "".join(f"<a class='b g li' href='/p/{k}'><b>📦 {k}</b><span>{lot_summary(k)['qty']} × {lot_summary(k)['product']} · {lot_summary(k)['site']}</span></a>" for k in lots)
    return f"""<!doctype html><title>ThreadTrace</title>{PHONE_CSS}
<style>.li{{text-align:left;display:flex;flex-direction:column;gap:2px;padding:14px 16px;margin-top:10px}}.li span{{color:#8696a8;font-size:14px;font-weight:400}}
select{{width:100%;padding:13px;border-radius:12px;border:1px solid #1d2834;background:#0e141c;color:#e9eff6;font-size:16px}}
.scan{{display:flex;flex-direction:column;align-items:center;gap:6px;padding:26px 16px;font-size:20px}} .scan small{{font-size:14px;font-weight:500;opacity:.8}}
h3{{margin:26px 0 0;font-size:15px;color:#8696a8;font-weight:600;text-transform:uppercase;letter-spacing:1px}}</style>
<h1>ThreadTrace</h1><p class=m>Customer value review · phone</p>
<select id=cu onchange="location.href='/m?cust='+this.value">{custs}</select>
<label class="b scan" for=cam>📷 Scan a QR code<small>package or garment · opens the camera</small></label>
<input id=cam type=file accept="image/*" capture=environment hidden>
<p class=m id=st style="text-align:center"></p>
<h3>Packages</h3>{lrows}
<h3>More</h3>
<a class="b g" href="/weigh">⚖ Weigh-in</a>
<a class="b g" href="/?attract=0&cust={cid}">📊 Open the review</a>
<a class="b g" href="/?attract=0&cust={cid}&report=1">📄 Read the annual report</a>
<canvas id=cv hidden></canvas>
<script>
const st=document.getElementById('st');
function load(src,next){{const s=document.createElement('script');s.src=src;s.onload=()=>window.jsQR?0:next&&next();s.onerror=()=>next&&next();document.head.appendChild(s);}}
load('/vendor/jsQR.min.js',()=>load('https://cdnjs.cloudflare.com/ajax/libs/jsQR/1.4.0/jsQR.min.js',()=>load('https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.min.js')));
function go(t){{
  let m=t.match(/(\\/(?:p|s)\\/[A-Za-z0-9_-]+|\\/weigh[^\\s]*)/); if(m) return location.href=m[1];
  m=t.match(/package:([A-Za-z0-9_-]+)/); if(m) return location.href='/p/'+m[1];
  st.textContent='This QR is not a ThreadTrace code: '+t.slice(0,60);
}}
document.getElementById('cam').onchange=e=>{{
  const f=e.target.files[0]; if(!f) return; st.textContent='Reading the QR code…';
  if(!window.jsQR){{st.textContent='QR reader not loaded yet. Try again in a second.';return;}}
  const img=new Image(); img.onload=()=>{{
    const cv=document.getElementById('cv'), x=cv.getContext('2d',{{willReadFrequently:true}});
    for(const max of [1200,800,1600]){{
      const k=Math.min(1,max/Math.max(img.width,img.height)); cv.width=img.width*k; cv.height=img.height*k; x.drawImage(img,0,0,cv.width,cv.height);
      const r=jsQR(x.getImageData(0,0,cv.width,cv.height).data,cv.width,cv.height); if(r){{st.textContent='Found '+r.data; return go(r.data);}}
    }}
    st.textContent='No QR code found. Hold the phone closer and try again.';
  }}; img.src=URL.createObjectURL(f);
}};
if({json.dumps(bool(scan))}) document.querySelector('.scan').animate([{{boxShadow:'0 0 0 0 rgba(56,189,248,.8)'}},{{boxShadow:'0 0 0 18px rgba(56,189,248,0)'}}],{{duration:1100,iterations:3}});
</script>"""


def page_cards(base):
    ids = DATA["demo_assets"]
    cards = []
    for lid in DATA.get("demo_lots", []):
        li = lot_summary(lid)
        cards.append(f"""<div class="card pk"><div class=qr data-u="{base}/p/{lid}"></div><b>📦 Package {lid}</b>
<span>{li['qty']} × {li['product']}<br>{li['site']}</span><span class=u>{base}/p/{lid}</span></div>""")
    for aid in ids:
        info = passport_summary(aid)
        cards.append(f"""<div class=card><div class=qr data-u="{base}/s/{aid}"></div><b>{info['product']}</b>
<span>{aid} · {info['site']}</span><span class=u>{base}/s/{aid}</span></div>""")
    cards.append(f"""<div class=card><div class=qr data-u="{base}/weigh"></div><b>Weigh-in station</b><span>Photograph the scale</span><span class=u>{base}/weigh</span></div>""")
    return f"""<!doctype html><meta charset=utf-8><title>ThreadTrace QR cards</title>
<script src="/vendor/qrcode.min.js"></script><script>window.QRCode||document.write('<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"><\\/script>')</script>
<style>body{{font-family:system-ui,sans-serif;margin:20px}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}}
.card{{border:1.5px dashed #999;border-radius:12px;padding:16px;display:flex;flex-direction:column;align-items:center;gap:6px;text-align:center;font-size:13px}}
.card b{{font-size:16px}}.pk{{border:2.5px solid #0b6fa4}}.u{{font-family:monospace;font-size:9px;color:#666;word-break:break-all}}.qr{{margin-bottom:8px}}
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
        m = re.fullmatch(r"/p/([A-Za-z0-9_-]+)", path)
        if m:
            q = parse_qs(urlparse(self.path).query)
            info = lot_summary(m.group(1))
            return self._send(200 if info else 404, page_lot(m.group(1), info, (q.get("act") or [""])[0], (q.get("g") or [""])[0]))
        if path == "/weigh":
            return self._send(200, PAGE_WEIGH)
        if path in ("/m", "/m/", "/phone"):
            q = parse_qs(urlparse(self.path).query)
            return self._send(200, page_home((q.get("cust") or [""])[0], (q.get("scan") or [""])[0]))
        if path == "/warehouse":
            return self._static("warehouse.html")
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
        if path == "/api/lot":
            info = lot_summary(str(body.get("lot_id", "")))
            action = body.get("action")
            if not info or action not in ("checkin", "repair", "writeoff"):
                return self._json({"ok": False, "note": "unknown package or action"}, 400)
            qty = max(1, min(int(body.get("qty") or 1), info["qty"]))
            loc = str(body.get("location") or info["site_id"])
            site = loc if any(s["id"] == loc for s in DATA["customers"][info["customer_id"]]["sites"]) else info["site_id"]
            ev = add_event({"type": action, "lot_id": info["id"], "qty": qty, "location": loc, "site_id": site,
                            "detail": str(body.get("detail") or "")[:60] or None,
                            "asset_id": body.get("asset_id") if body.get("asset_id") in DATA["passports"] else None,
                            "reader": "qr"})
            return self._json({"ok": True, "event": ev, "impact": lot_impact(info, action, qty)})
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
            lot = lot_summary(str(body.get("lot_id") or ""))
            ev = add_event({"type": "weigh", "kg": round(kg, 1), "photo": photo,
                            "reader": "gemini" if note == "gemini" else "manual",
                            "lot_id": lot["id"] if lot else None, "site_id": lot["site_id"] if lot else None})
            return self._json({"ok": True, "event": ev, "note": note, "impact": weigh_impact(ev["kg"])})
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
