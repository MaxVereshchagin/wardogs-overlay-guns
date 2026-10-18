"""Сервер оверлея Wardogs для OBS (запускается программой, если в settings.json "obs_server": true).

OBS:     Источник «Браузер» → http://127.0.0.1:8777/overlay.html  (1920×1080)

Адреса:
  /overlay.html        — плашка для OBS (?side=right — справа, ?demo — пример)
  /state               — что сейчас показывать (JSON)
  /set?weapon=AK74&att=CQB 74 Brake|RK6 Tactical Foregrip   — выставить вручную (для проверки)
  /clear               — спрятать плашку
  /quit                — выключить программу
"""
import json
import logging
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = 8777
HERE = os.path.dirname(os.path.abspath(__file__))
log = logging.getLogger("wardogs")   # тот же журнал, что у программы (overlay.log)

_lock = threading.Lock()
_state = {"weapon": None, "attachments": []}
quit_event = threading.Event()  # программа смотрит на него и закрывается


def set_state(weapon, attachments):
    """Вызывается распознаванием экрана (и вручную через /set)."""
    with _lock:
        _state["weapon"] = weapon
        _state["attachments"] = list(attachments)


def get_state():
    with _lock:
        return dict(_state)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/state":
            return self._json(get_state())
        if url.path == "/set":
            q = parse_qs(url.query)
            weapon = q.get("weapon", [None])[0]
            att = [a for a in q.get("att", [""])[0].split("|") if a]
            set_state(weapon, att)
            return self._json(get_state())
        if url.path == "/clear":
            set_state(None, [])
            return self._json(get_state())
        if url.path == "/quit":
            log.info("Сервер: пришла команда /quit от %s", self.client_address[0])
            quit_event.set()
            return self._json({"quit": True})
        return super().do_GET()

    def log_message(self, fmt, *args):
        # /state OBS спрашивает дважды в секунду — это не пишем, остальное — в журнал
        if not self.path.startswith("/state"):
            log.debug("Сервер: %s — %s", self.client_address[0], fmt % args)


def already_running():
    """Отвечает ли уже на нашем адресе другая копия программы."""
    import urllib.request
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/state", timeout=1).read()
        return True
    except Exception:
        return False


def start(background=False):
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    log.info("Сервер для OBS: http://127.0.0.1:%d/overlay.html", PORT)
    if background:
        threading.Thread(target=srv.serve_forever, daemon=True, name="сервер").start()
        return srv
    srv.serve_forever()


if __name__ == "__main__":
    start()
