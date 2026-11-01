"""Проверка английского интерфейса (2.0): плашка на английском — без русских букв (кроме названий из игры),
снимки — tests/screens/english/*.png; русский по-прежнему по-русски.
Запуск: python tests/test_english.py   (из папки D:\\WardogsOverlay)"""
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")
sys.path.insert(0, APP)
os.chdir(APP)
import wardogs_overlay as wo  # noqa: E402

wo.load_db()
ok = fail = 0


def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print("ОШИБКА:", name, info)


check("auto → ru или en", wo.pick_language("auto") in ("ru", "en"))
check("en → en", wo.pick_language("en") == "en")
check("ru → ru", wo.pick_language("ru") == "ru")

wo.LANG["code"] = "en"
wo.apply_language()
check("статы по-английски", dict((k, ru) for k, ru, _ in wo.STATS)["Vertical recoil"] == "Vertical recoil")
check("режимы оружейника", [m[0] for m in wo.GUNSMITH_MODES] == ["Balance", "Recoil", "ADS"])
check("колонки TTK", wo.TTK_COLS[0][1] == "NO ARMOR")

out = os.path.join(HERE, "screens", "english")
os.makedirs(out, exist_ok=True)
try:
    from PIL import ImageGrab
except Exception:
    ImageGrab = None
wo.save_settings = lambda changes: None
win = wo.OverlayWindow(dict(wo.DEFAULT_SETTINGS, x=40, y=40))
cyr = re.compile("[А-Яа-яЁё]")
muzzle = next(n for n, a in wo.ATTS.items() if a[1] == "Muzzle" and "M4" in a[3])
mag = next(n for n, a in wo.ATTS.items() if a[1] in wo.MAG_TYPES and "Super-45" in a[3])
cases = [
    ("1_m4_off", {"gunsmith": False, "detail": False},
     {"weapon": "M4", "attachments": [muzzle], "tab": "modules", "ttk": wo.ttk_payload("M4")}),
    ("2_m4_gunsmith_details", {"gunsmith": True, "gunsmith_mode": 0, "detail": True},
     {"weapon": "M4", "attachments": [muzzle], "tab": "modules", "ttk": wo.ttk_payload("M4")}),
    ("3_super45_ammo", {"gunsmith": True, "gunsmith_mode": 2, "detail": False},
     {"weapon": "Super-45", "attachments": [mag], "tab": "ammo", "ammo": "HollowPoint",
      "ttk": wo.ttk_payload("Super-45", "ammo", "HollowPoint")}),
    ("4_compare", {"gunsmith": False, "detail": False},
     {"weapon": "M4", "attachments": [muzzle], "saved": [], "tab": "modules", "ttk": wo.ttk_payload("M4")}),
]
for name, settings, state in cases:
    win.settings.update(settings)
    win.last = None
    win.render(state)
    win.root.update()
    time.sleep(0.3)
    win.root.update()
    texts = [win.canvas.itemcget(i, "text") for i in win.canvas.find_all() if win.canvas.type(i) == "text"]
    russian = [t for t in texts if cyr.search(t)]
    check(f"{name}: на плашке нет русских букв", not russian, russian)
    if ImageGrab:
        x, y = win.root.winfo_rootx(), win.root.winfo_rooty()
        ImageGrab.grab((x, y, x + win.W, y + win.canvas.winfo_height()), all_screens=True).save(
            os.path.join(out, name + ".png"))
win.flash(wo.T("ОВЕРЛЕЙ WARDOGS ЗАПУЩЕН", "WARDOGS OVERLAY STARTED"), "Menu search — Ctrl+Shift+O")
win.root.update()
win.root.destroy()
print(f"Английский: {ok} проверок прошло, ошибок {fail}")
sys.exit(1 if fail else 0)
