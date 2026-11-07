# ThreadTrace

**Scan one garment. See the value of the whole account.**

Lindström x Since AI Hackathon 2026 – *Customer Value & Sustainability Review Generator*.

ThreadTrace turns the multi-site data of a textile-service customer into a live, customer-specific annual review. The review starts from one physical garment. Someone scans its QR code with a phone, and the on-site gateway (an Arduino UNO Q) records the scan, lights its LED matrix, and pushes the event to the review screen. The screen then zooms out in three scenes: the garment's year of service cycles, its delivery site, and the whole account (24 sites for NorthStar Foods). Every number carries a status label (simulated, estimated, team assumption, public reference or field scan) and opens its formula on click.

## What it shows

| Area (from Lindström's evidence list) | In ThreadTrace |
| --- | --- |
| Customer success scorecard | Red / amber / green account health (service, adoption, sustainability) |
| Service performance | % of cycles delivered on time, turnaround hours, late cycles per site |
| Service adoption | Garments with scan activity; "allocated garments with no scan activity" as a signal to investigate |
| Operational value | Repairs by type, replacements postponed |
| Financial value | Purchase cost avoided by repairs (low / base / high, estimated) |
| Sustainability value | Water and CO₂e saved vs a selectable baseline (local laundry, home washing, in-house laundry), low / base / high |
| Improvement / future | Rules-generated observations and steering-committee questions, contract KPIs the KAM adds |
| Field evidence | Live QR scans and weigh-ins captured by the UNO Q gateway |
| Package tracking | One big QR per package (≤25 garments of one style): customer, site, quantity, production and delivery date. Each scan adds the place (check in), and repairs / write-offs are reported with a quantity. The screen shows money saved: repaired garments × purchase cost |

Personalisation: a priority switch (Water / Emissions / Circularity & cost / Service) changes the headline, map, chart and order of observations. The default priority follows the customer's industry, e.g. water for Food & Beverage. **Export report** produces a print-ready PDF annual review that includes the assumptions and limitations.

### Package tracking

Scanning every garment is not realistic in daily work. So each package gets one big QR (`/p/PK0001`). It holds the same garments as the small QR codes inside.

- **Check in**: the phone picks the place (customer site or Lindström laundry). This gives location history per package.
- **Repair / write-off**: scan the package (or one garment's QR, which links to its package), type the number of garments and the reason.
- **Weigh**: `/weigh?lot=PK0001` links a weigh-in to the package.
- **Package room** (`/warehouse.html`, key **P** on the big screen): a simulated workshop shelf. Tap a box to open it, see its QR and location, and simulate a phone scan.
- **Phone home** (`/m`): every phone page has Back / Home / Scan. Scan opens the camera and reads the QR from the photo (jsQR), so the phone can always get back to scanning.
- Money saved = repaired garments × purchase cost (estimated). Production and delivery dates are demo values (T7). Cleaning and delivery prices are a data gap (T8).

## Architecture

```
iPhone camera ── scans garment QR ─────────► UNO Q (Linux side, server.py)
iPhone browser ─ photo of scale (/weigh) ──►  · records events in SQLite
                                              · reads the scale photo with Gemini (optional)
                                              · serves the dashboard + data.json
                                              · Server-Sent Events → dashboard
                                              · /led → App Lab app → MCU → LED matrix
                                                        │
Laptop browser (full screen) ◄──────────────────────────┘  dashboard: index.html
Laptop (once): tools/build_data.py  Excel data pack → data.json
```

## Run it

### On a laptop (no board needed)
```bash
python tools/build_data.py Lindstrom_SinceAI_Customer_Value_Synthetic_Data_Pack.xlsx board/static/data.json   # needs pandas + openpyxl
cd board && python server.py
```
Open http://localhost:8000. Press **S** to simulate a garment scan and **W** to simulate a weigh-in.

### On the Arduino UNO Q
```powershell
# from the laptop (Windows PowerShell), copy the board folder
scp -r board arduino@<board-tailscale-ip>:~/threadtrace
```
```bash
# on the board (ssh arduino@<board-tailscale-ip>)
cd ~/threadtrace
echo "GEMINI_API_KEY=your_key_here" > .env     # optional: enables reading the scale photo
./run.sh                                       # prints the dashboard URL
```
- Dashboard: `http://<board-ip>:8000/` (full screen with F11)
- QR cards to print: `http://<board-ip>:8000/cards`
- Phone weigh-in page: `http://<board-ip>:8000/weigh`
- The phone needs to reach the board: install Tailscale on the iPhone and log in to the same tailnet.

### LED matrix (optional)
In Arduino App Lab create a new app, paste `led-app/python/main.py` and `led-app/sketch/sketch.ino`, and run it. It polls the gateway at `http://172.17.0.1:8000/led` (the Docker host address) and scrolls the latest event.

## Data and honesty rules
- All customer, site and operational data are **synthetic** (Lindström x Since AI data pack). Nothing is presented as audited or as an approved customer claim.
- Low / base / high ranges are **TEAM_ASSUMPTION** values (see `docs/methodology.md`), following Lindström's guidance on Discord.
- QR codes stand in for the UHF RFID tags used in production. Weigh-ins are demo captures; in production, washed kg is measured at the laundry.

See `docs/methodology.md` and `docs/data-readiness.md`.
