"""Проверка 2.1 на английском: новые надписи (Оружейник — уровни и «don't have», чистка, подсказки) без русских букв.
Снимки — tests/screens/en21/*.png.  Запуск: python -X utf8 tests/test_en_21.py"""
import os
import re
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
wo.LANG["code"] = "en"
wo.apply_language()
cyr = re.compile("[А-Яа-яЁё]")
ok = fail = 0
out = os.path.join(HERE, "screens", "en21")
os.makedirs(out, exist_ok=True)


def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print("ОШИБКА:", name, info)


def snap(w, name):
    w.update()
    time.sleep(0.4)
    w.update()
    x, y = w.winfo_rootx(), w.winfo_rooty()
    with mss.mss() as sct:
        img = np.array(sct.grab({"left": x, "top": y, "width": w.winfo_width(), "height": w.winfo_height()}))[:, :, :3]
    cv2.imencode(".png", img)[1].tofile(os.path.join(out, name + ".png"))


def texts(w):
    res = []
    for ch in w.winfo_children():
        cls = ch.winfo_class()
        if cls == "Label":
            res.append(ch.cget("text"))
        elif cls == "Canvas":
            res += [ch.itemcget(i, "text") for i in ch.find_all() if ch.type(i) == "text"]
        res += texts(ch)
    return [t for t in res if t]


check("слоты Оружейника по-английски", [g[0] for g in wo.GUNSMITH_SLOTS] == ["MUZZLE", "GRIP"], wo.GUNSMITH_SLOTS)
settings = dict(wo.DEFAULT_SETTINGS, x=40, y=40, gunsmith=True)
win = wo.OverlayWindow(settings)
best = wo.gunsmith_advice("M4", [], 0)[0][0][1]
for name, upd in (("plate_plain", {"levels": None, "missing": []}),
                  ("plate_missing", {"levels": None, "missing": [best]}),
                  ("plate_all_locked", {"levels": {k: 0 for k, *_ in wo.ROLES}, "missing": []})):
    settings.update(upd)
    win.last = None
    win.render({"weapon": "M4", "attachments": [], "tab": "modules", "ttk": True})
    snap(win.root, name)
    t = [win.canvas.itemcget(i, "text") for i in win.canvas.find_all() if win.canvas.type(i) == "text"]
    rus = [x for x in t if cyr.search(x)]
    check(f"{name}: нет русских букв", not rus, rus)
top = wo.settings_window(win.root, settings, lambda: None, win, {"rec": None, "updating": False})
top.update()
time.sleep(0.6)
rus = [x for x in texts(top) if cyr.search(x) and x != "Русский"]
check("пульт: нет русских букв", not rus, rus)
check("пульт: роли по-английски", all(any(en == x for x in texts(top)) for _, _, en, _ in wo.ROLES), texts(top))
top.update()
snap(top, "pult")
top.destroy()
win.root.destroy()
print(f"EN 2.1: {ok} проверок прошло, ошибок {fail}")
sys.exit(1 if fail else 0)
