# ThreadTrace LED app (Arduino App Lab, Python side).
# Polls the ThreadTrace gateway running on the board host and tells the MCU sketch which animation to play.
# 172.17.0.1 is the Docker host address seen from inside the App Lab container.
import json
import threading
import time
import urllib.request

from arduino.app_utils import App, Bridge

URL = "http://172.17.0.1:8000/led"


def poll():
    last = None
    while True:
        try:
            with urllib.request.urlopen(URL, timeout=3) as r:
                d = json.load(r)
            if last is None:
                last = d["seq"]  # don't replay the last event on start-up
            elif d["seq"] != last:
                last = d["seq"]
                Bridge.call("show", int(d.get("kind", 0)), int(d.get("a", 0)), int(d.get("b", 0)))
                print("LED ->", d)
        except Exception as e:  # gateway not running yet, etc.
            print("led poll:", e)
        time.sleep(0.4)


threading.Thread(target=poll, daemon=True).start()
App.run()
