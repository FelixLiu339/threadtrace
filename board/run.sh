#!/usr/bin/env bash
# Start the ThreadTrace gateway in the background on the UNO Q.
# Put your key in ~/threadtrace/.env as:  GEMINI_API_KEY=xxxx
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
mkdir -p static/vendor
[ -s static/vendor/qrcode.min.js ] || curl -fsSL -m 20 -o static/vendor/qrcode.min.js https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js || echo "note: could not download the QR library; pages will try the CDN"
[ -s static/vendor/jsQR.min.js ] || curl -fsSL -m 20 -o static/vendor/jsQR.min.js https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.min.js || echo "note: could not download the QR reader; the phone page will try the CDN"
pkill -f "python3 server.py" 2>/dev/null
nohup python3 server.py > server.log 2>&1 &
sleep 1
tail -n 3 server.log
echo "Dashboard: http://$(tailscale ip -4 2>/dev/null || hostname -I | awk '{print $1}'):8000/"
