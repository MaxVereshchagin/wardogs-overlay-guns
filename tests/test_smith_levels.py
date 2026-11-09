"""Проверка 2.1: Оружейник советует только доступное — «мои уровни» (пульт) и «✕ нет у меня» (плашка).
Снимки плашки — tests/screens/smith_levels/*.png, лог — overlay_test.log.
Запуск: python -X utf8 tests/test_smith_levels.py   (из папки D:\\WardogsOverlay)"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")
sys.path.insert(0, APP)
os.chdir(APP)
import wardogs_overlay as wo  # noqa: E402

wo.setup_log("overlay_test.log", console_log=False)
wo.load_db()
wo.save_settings = lambda c: None
ok = fail = 0
t0 = time.time()


def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print("ОШИБКА:", name, info)
    wo.log.info("ТЕСТ %s: %s %s", "ок" if cond else "ОШИБКА", name, info if not cond else "")


ZERO = {k: 0 for k, *_ in wo.ROLES}
MAX = {k: m for k, _, _, m in wo.ROLES}

# 1. база знает уровни открытия
check("в базе есть уровни открытия", len(wo.UNLOCKS) > 50, len(wo.UNLOCKS))
check("роли в базе — только известные", {u[0] for u in wo.UNLOCKS.values()} <= {r[0] for r in wo.ROLES},
      {u[0] for u in wo.UNLOCKS.values()})

# 2. без уровней и «нет у меня» — совет как раньше (2.0.1)
for weapon in wo.WEAPONS:
    for mode in range(3):
        old = wo.gunsmith_advice(weapon, [], mode)
        new = wo.gunsmith_advice(weapon, [], mode, wo.smith_filter({}))
        check(f"{weapon} режим {mode}: без фильтра совет не изменился", old == new, (old, new))

# 3. уровни 0 — советуем только открытое сразу; максимум — как без фильтра
flt0 = wo.smith_filter({"levels": ZERO})
fltmax = wo.smith_filter({"levels": MAX})
changed = 0
for weapon in wo.WEAPONS:
    adv0, _ = wo.gunsmith_advice(weapon, [], 0, flt0)
    for label, best, cur in adv0:
        check(f"{weapon} {label}: на нулевых уровнях советует открытое сразу", best not in wo.UNLOCKS, (best, wo.UNLOCKS.get(best)))
    check(f"{weapon}: максимум уровней = без фильтра",
          wo.gunsmith_advice(weapon, [], 0, fltmax) == wo.gunsmith_advice(weapon, [], 0), weapon)
    if adv0 != wo.gunsmith_advice(weapon, [], 0)[0]:
        changed += 1
check("уровни 0 меняют совет хоть у части пушек", changed > 0, changed)
print(f"На нулевых уровнях совет поменялся у {changed} из {len(wo.WEAPONS)} пушек")

# 4. то, что уже стоит на пушке, советовать можно всегда (оно у игрока есть)
full, _ = wo.gunsmith_advice("M4", [], 0)
best_muzzle = full[0][1]
adv, _ = wo.gunsmith_advice("M4", [best_muzzle], 0, flt0)
check("стоящий обвес не прячется уровнями", adv[0][1] == best_muzzle and adv[0][2] == best_muzzle, adv)

# 5. «нет у меня»: лучший пропадает, советуется следующий; несколько отметок подряд
miss = []
seen = []
for _ in range(3):
    adv, _ = wo.gunsmith_advice("M4", [], 0, wo.smith_filter({"missing": miss}))
    if not adv or adv[0][0] != "ДУЛО":
        break
    b = adv[0][1]
    check(f"«нет у меня»: {b} ещё не отмечен", b not in miss, (b, miss))
    seen.append(b)
    miss = miss + [b]
check("«нет у меня» каждый раз даёт новый обвес", len(set(seen)) == len(seen) == 3, seen)
print("M4, дуло по очереди с «нет у меня»:", " → ".join(seen))

# 6. уровни считаются по ролям правильно
some = next(n for n, u in wo.UNLOCKS.items() if u[1] > 1 and any(n in wo.ATTS and w in wo.ATTS[n][3] for w in wo.WEAPONS))
role, need = wo.UNLOCKS[some][:2]
check(f"{some}: на {need - 1} уровне {role} закрыт", not wo.smith_available(some, wo.smith_filter({"levels": {role: need - 1}})))
check(f"{some}: на {need} уровне {role} открыт", wo.smith_available(some, wo.smith_filter({"levels": {role: need}})))
check(f"{some}: без уровней доступен", wo.smith_available(some, wo.smith_filter({})))

# 7. плашка: снимки (уровни 0, «нет у меня», всё закрыто) + зона клика «нет у меня» добавляет обвес
out = os.path.join(HERE, "screens", "smith_levels")
os.makedirs(out, exist_ok=True)
import cv2  # noqa: E402
import mss  # noqa: E402
import numpy as np  # noqa: E402
settings = dict(wo.DEFAULT_SETTINGS, x=40, y=40, gunsmith=True)
win = wo.OverlayWindow(settings)
state = {"weapon": "M4", "attachments": [], "tab": "modules", "ttk": True}


def snap(name, upd):
    settings.update(upd)
    win.last = None
    win.render(dict(state))
    win.root.update()
    time.sleep(0.4)
    win.root.update()
    x, y = win.root.winfo_rootx(), win.root.winfo_rooty()
    with mss.mss() as sct:
        img = np.array(sct.grab({"left": x, "top": y, "width": win.root.winfo_width(), "height": win.root.winfo_height()}))
    ok_png, png = cv2.imencode(".png", img[:, :, :3])
    png.tofile(os.path.join(out, name + ".png"))
    c = win.canvas
    right = max((c.bbox(i)[2] for i in c.find_all() if c.type(i) == "text"), default=0)
    check(f"снимок {name}: текст не вылезает за край", right <= win.W + 1, right)


snap("1_без_фильтра", {"levels": None, "missing": []})
zones = [z for z in win.zones if "missing" in z[4]]
check("на плашке есть «✕ нет у меня»", len(zones) >= 1, win.zones)
if zones:
    check("клик «нет у меня» отмечает советуемый обвес", zones[0][4]["missing"] == [best_muzzle], zones[0][4])
snap("2_уровни_0", {"levels": ZERO, "missing": []})
snap("3_нет_у_меня", {"levels": None, "missing": [best_muzzle]})
check("строка «вернуть все» есть", any(z[4] == {"missing": []} for z in win.zones), win.zones)
everything = [n for n, a in wo.ATTS.items() if "M4" in a[3]]
snap("4_всё_скрыто", {"levels": ZERO, "missing": everything})
win.root.destroy()

print(f"Итого: {ok} ок, {fail} ошибок, {time.time() - t0:.1f} с. Снимки: {out}")
wo.log.info("ТЕСТ test_smith_levels: %d ок, %d ошибок", ok, fail)
sys.exit(1 if fail else 0)
