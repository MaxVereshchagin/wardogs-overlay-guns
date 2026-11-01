"""Проверка кнопок дистанции TTK (1.6): у кого урон держит — «Урон держит до … м», у кого падает — кнопки;
клик по кнопке пересчитывает TTK. Снимки — tests/screens/ttk_dist/*.png.
Запуск: python tests/test_ttk_dist.py   (из папки D:\\WardogsOverlay)"""
import os
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


EXPECT = {"M4": (None, 500), "AK74": (None, 500), "M249 SAW": (None, 500), "M14": (None, 500),
          "SVD": (None, 800), "SV98": (None, 1000), "Super-45": ([50, 75, 100], None),
          "MP5": ([50, 150, 175], None), "GGX 18": ([50, 75, 100], None), "Deagle": ([50, 100, 125], None),
          "PP-19 Bizon": ([50, 250, 300], None)}
for w, exp in EXPECT.items():
    got = wo.ttk_distances(w)
    check(f"{w}: дистанции", got == exp, f"{got} ≠ {exp}")

# у всех пушек с баллистикой план считается без ошибок, кнопки идут по возрастанию
for w in wo.WEAPONS:
    try:
        b, hold = wo.ttk_distances(w)
        if b:
            check(f"{w}: кнопки по возрастанию", b == sorted(set(b)), b)
            check(f"{w}: первая кнопка 50 м", b[0] == 50, b)
            counts = [[c[0] for c in wo.ttk_cells(w, "FMJ", "torso_upper", d)] for d in b]
            check(f"{w}: на соседних кнопках разное число пуль",
                  all(counts[i] != counts[i + 1] for i in range(len(counts) - 1)), list(zip(b, counts)))
    except Exception as e:
        check(f"{w}: план без ошибки", False, repr(e))

# TTK на 50 м совпадает со старым (как в ролике «Топ-3»): M14, броня 3, тело — 5 выстрелов, 0,40 с
cells = wo.ttk_cells("M14", "FMJ", "torso_upper", 50)
check("M14 броня 3 на 50 м = 5 выстрелов", cells[3][0] == 5, cells)
check("M14 броня 3 на 50 м ≈ 0,40 с", abs(cells[3][1] - 0.40) < 0.015, cells)

out = os.path.join(HERE, "screens", "ttk_dist")
os.makedirs(out, exist_ok=True)
try:
    from PIL import ImageGrab
except Exception:
    ImageGrab = None
wo.save_settings = lambda changes: None          # в settings.json тест ничего не пишет
win = wo.OverlayWindow(dict(wo.DEFAULT_SETTINGS, x=40, y=40))


def shot(name, state):
    win.last = None
    win.render(state)
    win.root.update()
    time.sleep(0.3)
    win.root.update()
    if ImageGrab:
        x, y = win.root.winfo_rootx(), win.root.winfo_rooty()
        ImageGrab.grab((x, y, x + win.W, y + win.canvas.winfo_height()), all_screens=True).save(
            os.path.join(out, name + ".png"))


m4 = {"weapon": "M4", "attachments": [], "tab": "modules", "ttk": wo.ttk_payload("M4")}
shot("1_M4_держит", m4)
check("M4: кнопок дистанции нет", not any("ttk_dist" in z[4] for z in win.zones), win.zones)
sp = {"weapon": "Super-45", "attachments": [], "tab": "modules", "ttk": wo.ttk_payload("Super-45")}
shot("2_Super45_50м", sp)
zones = [z for z in win.zones if "ttk_dist" in z[4]]
check("Super-45: две кликабельные кнопки (выбранная не кликается)", len(zones) == 2, zones)
z100 = next(z for z in zones if z[4]["ttk_dist"]["Super-45"] == 100)


class E:
    x, y = (z100[0] + z100[2]) // 2, (z100[1] + z100[3]) // 2


win._on_click(E)
win.root.update()
check("клик 100 м запомнился", win.settings["ttk_dist"].get("Super-45") == 100, win.settings.get("ttk_dist"))
check("TTK пересчитан на 100 м", win.state["ttk"]["dist"] == 100 and win.state["ttk"]["rows"][0][1][0][0] == 8,
      win.state["ttk"])
shot("3_Super45_100м", sp)
ammo = {"weapon": "Super-45", "attachments": [], "tab": "ammo", "ammo": "FMJ",
        "ttk": wo.ttk_payload("Super-45", "ammo", "FMJ")}
shot("4_Super45_боеприпасы_100м", ammo)
check("вкладка патронов тоже на 100 м", win.state["ttk"]["dist"] == 100, win.state["ttk"].get("dist"))
for d in (50, 250, 300):     # Бизон: 50 м, дальше — только там, где пуль больше
    win.settings["ttk_dist"]["PP-19 Bizon"] = d
    shot(f"5_Бизон_боеприпасы_{d}м", {"weapon": "PP-19 Bizon", "attachments": [], "tab": "ammo", "ammo": "FMJ",
                                      "ttk": wo.ttk_payload("PP-19 Bizon", "ammo", "FMJ", d)})
win.root.destroy()
print(f"Дистанция TTK: {ok} проверок прошло, ошибок {fail}")
sys.exit(1 if fail else 0)
