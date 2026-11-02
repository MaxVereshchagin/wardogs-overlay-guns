"""Плашка при масштабе Windows 125 % и 150 % (подписчик 02.11: текст вылезал за край).
Настоящий масштаб Windows из теста не поменять, поэтому Tk заставляем считать, что он 150 % («сломано»), а потом
вызываем исправление fix_dpi() — как при запуске программы. Снимки — tests/screens/dpi/*.png, лог — overlay_test.log.
Проверка: ни одна надпись плашки не выходит за её правый край, ширины текста как при 100 %.
Запуск: python -X utf8 tests/test_dpi.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")
sys.path.insert(0, APP)
os.chdir(APP)
import wardogs_overlay as wo  # noqa: E402
from PIL import ImageGrab  # noqa: E402

OUT = os.path.join(HERE, "screens", "dpi")
os.makedirs(OUT, exist_ok=True)
wo.setup_log("overlay_test.log", console_log=True)
wo.load_db()
wo.save_settings = lambda c: None

STATE = {"weapon": "AK74", "attachments": ["AK74 30 RND Magazine", "Spitfire 3X", "TDG Vertical Foregrip"],
         "tab": "modules", "ammo": "FMJ"}


def overflow(win):
    """Сколько пикселей самая широкая надпись вылезает за правый край плашки (0 — всё помещается)."""
    c = win.canvas
    worst = 0
    for item in c.find_all():
        if c.type(item) == "text":
            bb = c.bbox(item)
            if bb and bb[1] >= 0:
                worst = max(worst, bb[2] - win.W)
    return worst


def shot(win, name, scaling, fix):
    win.root.tk.call("tk", "scaling", scaling)
    if fix:
        win.fix_dpi()
    win.last = None
    st = dict(STATE, ttk=wo.ttk_payload(STATE["weapon"], STATE["tab"], STATE["ammo"]))
    win.render(st)
    for _ in range(4):
        win.root.update()
        time.sleep(0.15)
    x, y = win.root.winfo_rootx(), win.root.winfo_rooty()
    ImageGrab.grab((x, y, x + win.W, y + win.canvas.winfo_height()), all_screens=True).save(os.path.join(OUT, name))
    return overflow(win), win.canvas.winfo_height()


def main():
    settings = dict(wo.DEFAULT_SETTINGS, x=40, y=40, opacity=1.0, gunsmith=True, gunsmith_mode=0, detail=True,
                    ttk_dist={})
    win = wo.OverlayWindow(settings)
    errors = 0
    base = shot(win, "100.png", 96 / 72, False)
    print(f"100 %: вылезает {base[0]} px, высота {base[1]}")
    for pct in (125, 150):
        sc = pct / 100 * 96 / 72
        broken = shot(win, f"{pct}_было.png", sc, False)
        fixed = shot(win, f"{pct}_стало.png", sc, True)
        ok = fixed[0] <= base[0] and fixed[1] == base[1]
        errors += not ok
        print(f"{pct} %: БЫЛО вылезает {broken[0]} px (высота {broken[1]}) -> СТАЛО {fixed[0]} px (высота {fixed[1]}) "
              f"— {'OK' if ok else 'ОШИБКА'}")
    win.root.destroy()
    print("ошибок:", errors)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
