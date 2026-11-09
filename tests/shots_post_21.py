"""Снимки для поста в ТГ про 2.1: плашка (уровни включены, «дальше», «нет у меня») и карточка «Оружейник» из пульта.
Непрозрачность 100 %, чтобы не просвечивал рабочий стол. Кладёт PNG в D:\дляклауда\telegram\посты\оверлей_2.1\src\\"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")
sys.path.insert(0, APP)
os.chdir(APP)
import wardogs_overlay as wo  # noqa: E402
import cv2  # noqa: E402
import mss  # noqa: E402
import numpy as np  # noqa: E402

wo.setup_log("overlay_test.log", console_log=False)
wo.load_db()
wo.save_settings = lambda c: None
wo.message = lambda t: None
OUT = r"D:\дляклауда\telegram\посты\оверлей_2.1\src"
os.makedirs(OUT, exist_ok=True)


def grab(x, y, w, h, name):
    with mss.mss() as sct:
        img = np.array(sct.grab({"left": x, "top": y, "width": w, "height": h}))[:, :, :3]
    cv2.imencode(".png", img)[1].tofile(os.path.join(OUT, name + ".png"))


def settle(w):
    for _ in range(10):
        w.update()
        time.sleep(0.05)


levels = {"Career": 5, "Infantry": 8, "Medic": 5, "Recon": 5, "Support": 5}
settings = dict(wo.DEFAULT_SETTINGS, x=40, y=40, gunsmith=True, opacity=1.0, levels=levels)
win = wo.OverlayWindow(settings)
win.root.attributes("-alpha", 1.0)
for name, weapon, atts, upd in (("plate_levels", "AK74", [], {}),
                                ("plate_missing", "M4", [], {"levels": None, "missing": ["3-Chamber Brake"]})):
    settings.update(upd)
    win.last = None
    win.render({"weapon": weapon, "attachments": atts, "tab": "modules", "ttk": True})
    settle(win.root)
    grab(win.root.winfo_rootx(), win.root.winfo_rooty(), win.root.winfo_width(), win.root.winfo_height(), name)
settings.update({"levels": {"Career": 12, "Infantry": 8, "Medic": 3, "Recon": 0, "Support": 5}, "missing": []})
win.root.withdraw()
top = wo.settings_window(win.root, settings, lambda: None, win, {"rec": None, "updating": False})
settle(top)
lbl = None


def find(w):
    global lbl
    for ch in w.winfo_children():
        if ch.winfo_class() == "Label" and ch.cget("text") == "ОРУЖЕЙНИК":
            lbl = ch
        find(ch)


find(top)
card = lbl.master.master.master          # подпись → шапка → inner → рамка карточки
settle(top)
grab(card.winfo_rootx(), card.winfo_rooty(), card.winfo_width(), card.winfo_height(), "pult_smith")
top.destroy()
win.root.destroy()
print("ok", os.listdir(OUT))
