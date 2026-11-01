"""Проверка «Оружейника» (1.6): совет по слотам, сборка с советом, строки статов (4 главных / подробно)
и отрисовка плашки в окне — снимки состояний в tests/screens/gunsmith/*.png.
Запуск: python tests/test_gunsmith.py   (из папки D:\\WardogsOverlay)"""
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


# 1. совет для M4 во всех режимах: два слота, обвесы подходят к M4 и стоят в своём слоте
for mode in range(3):
    advice, build = wo.gunsmith_advice("M4", [], mode)
    check(f"M4 режим {mode}: два слота", [a[0] for a in advice] == ["ДУЛО", "РУКОЯТЬ"], advice)
    for label, best, cur in advice:
        a = wo.ATTS[best]
        check(f"M4 {label}: подходит к M4", "M4" in a[3], best)
        check(f"M4 {label}: сейчас пусто", cur is None, cur)
    check(f"M4 режим {mode}: сборка = 2 обвеса", len(build) == 2, build)

# 2. режим «Отдача» даёт отдачу не хуже «Баланса», «ADS» — время ADS не хуже «Баланса»
b0 = wo.stat_totals(wo.gunsmith_advice("M4", [], 0)[1], "M4")[0]
b1 = wo.stat_totals(wo.gunsmith_advice("M4", [], 1)[1], "M4")[0]
b2 = wo.stat_totals(wo.gunsmith_advice("M4", [], 2)[1], "M4")[0]
rec = lambda t: t.get("Vertical recoil", 1) * t.get("Horizontal recoil", 1)
check("Отдача <= Баланс по отдаче", rec(b1) <= rec(b0) + 1e-9, (rec(b1), rec(b0)))
check("ADS <= Баланс по времени ADS", b2.get("ADS time", 1) <= b0.get("ADS time", 1) + 1e-9)

# 3. стоящий обвес в советуемом слоте заменяется, остальные (прицел, магазин) остаются
some_optic = next(n for n, a in wo.ATTS.items() if a[1] == "Optic" and "M4" in a[3])
some_muzzle = next(n for n, a in wo.ATTS.items() if a[1] == "Muzzle" and "M4" in a[3])
advice, build = wo.gunsmith_advice("M4", [some_optic, some_muzzle], 0)
check("прицел остался в сборке с советом", some_optic in build, build)
check("в слоте дула — совет", advice[0][1] in build and advice[0][2] == some_muzzle, advice)
check("дуло в сборке одно", sum(1 for n in build if wo.ATTS[n][1] in ("Muzzle", "Suppressor")) == 1, build)

# 4. все пушки: совет не падает, в сборке нет обвесов, не подходящих пушке
for weapon in wo.WEAPONS:
    try:
        advice, build = wo.gunsmith_advice(weapon, [], 0)
        check(f"{weapon}: обвесы подходят", all(weapon in wo.ATTS[n][3] for n in build), build)
    except Exception as e:
        check(f"{weapon}: совет без ошибки", False, repr(e))

# 5. строки: без «Подробно» ровно 4 главных, с «Подробно» — только меняющиеся
rows4 = wo.compute_rows([], "M4", wo.KEY_STATS)
check("4 главных строки без обвесов", len(rows4) == 4, rows4)
check("ADS без обвесов — секунды", rows4[-1][3] == "0,49 с", rows4[-1])
check("подробно без обвесов — пусто", wo.compute_rows([], "M4") == [])

# 6. отрисовка в окне: три состояния → снимки
out = os.path.join(HERE, "screens", "gunsmith")
os.makedirs(out, exist_ok=True)
settings = dict(wo.DEFAULT_SETTINGS, x=40, y=40)
win = wo.OverlayWindow(settings)
state = {"weapon": "M4", "attachments": [some_muzzle], "tab": "modules", "ttk": wo.ttk_payload("M4")}
try:
    from PIL import ImageGrab
except Exception:
    ImageGrab = None
# строки «сейчас / с советом»: поставил советуемое — строки совпадают; другое — расходятся
adv, advised = wo.gunsmith_advice("M4", [], 0)
same = wo.compute_pair_rows(advised, advised, "M4", wo.KEY_STATS)
check("совет стоит — сейчас = с советом", all(abs(r[2] - r[4]) < 1e-9 for r in same), same)
diff = wo.compute_pair_rows([], advised, "M4", wo.KEY_STATS)
check("без обвесов — совет что-то меняет", any(abs(r[2] - r[4]) > 0.004 for r in diff), diff)
grip = next(n for n, a in wo.ATTS.items() if a[1] == "Foregrip" and "M4" in a[3] and n != adv[1][1])
for name, changes, atts in (("1_выключен", {"gunsmith": False, "detail": False}, [some_muzzle]),
                            ("2_баланс", {"gunsmith": True, "gunsmith_mode": 0, "detail": False}, [some_muzzle]),
                            ("2б_баланс_другая_рукоять", {"gunsmith": True, "gunsmith_mode": 0, "detail": False},
                             [some_muzzle, grip]),
                            ("2в_баланс_всё_как_советует", {"gunsmith": True, "gunsmith_mode": 0, "detail": False},
                             list(advised)),
                            ("3_ads_подробно", {"gunsmith": True, "gunsmith_mode": 2, "detail": True}, [some_muzzle])):
    state = {"weapon": "M4", "attachments": atts, "tab": "modules", "ttk": wo.ttk_payload("M4")}
    win.settings.update(changes)
    win.last = None
    win.render(state)
    win.root.update()
    time.sleep(0.3)
    win.root.update()
    check(f"{name}: есть кликабельные зоны", len(win.zones) == (4 if changes["gunsmith"] else 2), win.zones)
    if ImageGrab:
        x, y = win.root.winfo_rootx(), win.root.winfo_rooty()
        ImageGrab.grab((x, y, x + win.W, y + win.canvas.winfo_height()), all_screens=True).save(
            os.path.join(out, name + ".png"))
# вкладка «Боеприпасы»: те же статы с советом, оружейник и «Подробно»
mag = next(n for n, a in wo.ATTS.items() if a[1] in wo.MAG_TYPES and "M4" in a[3])
ammo_state = {"weapon": "M4", "attachments": [mag], "tab": "ammo", "ammo": "FMJ",
              "ttk": wo.ttk_payload("M4", "ammo", "FMJ")}
win.settings.update(gunsmith=True, gunsmith_mode=0, detail=False)
win.last = None
win.render(ammo_state)
win.root.update()
time.sleep(0.3)
win.root.update()
check("боеприпасы: есть «Подробно» и оружейник",
      any(z[4] == {"detail": True} for z in win.zones) and any("gunsmith" in z[4] for z in win.zones), win.zones)
if ImageGrab:
    x, y = win.root.winfo_rootx(), win.root.winfo_rooty()
    ImageGrab.grab((x, y, x + win.W, y + win.canvas.winfo_height()), all_screens=True).save(
        os.path.join(out, "4_боеприпасы.png"))
# клик по кнопке «Отдача» меняет режим (настройки в файл не пишем — подменяем сохранение)
wo.save_settings = lambda changes: None
win.settings.update(gunsmith=True, gunsmith_mode=0)
win.last = None
win.render(state)
z = next(z for z in win.zones if z[4] == {"gunsmith_mode": 1})


class E:
    x, y = (z[0] + z[2]) // 2, (z[1] + z[3]) // 2


win._on_click(E)
check("клик «Отдача» переключил режим", win.settings["gunsmith_mode"] == 1)
win.root.destroy()
print(f"Оружейник: {ok} проверок прошло, ошибок {fail}")
sys.exit(1 if fail else 0)
