# ThreadTrace LED app (Arduino App Lab, Python side).
# Polls the ThreadTrace gateway on the board host and sends the text to the MCU sketch over Bridge.
# 172.17.0.1 is the Docker host address seen from inside the App Lab container.
import json
import threading
import time
import urllib.request

from arduino.app_utils import App, Bridge

URL = "http://172.17.0.1:8000/led"


def poll():
    last = -1
    while True:
        try:
            with urllib.request.urlopen(URL, timeout=3) as r:
                d = json.load(r)
            if d["seq"] != last:
                last = d["seq"]
                Bridge.call("show", d["text"])
                print("LED ->", d["text"])
        except Exception as e:  # gateway not running yet, etc.
            print("led poll:", e)
        time.sleep(0.5)


threading.Thread(target=poll, daemon=True).start()
App.run()
