"""Плашка для OBS (1.8): сервер отдаёт готовую плашку (view) — те же данные, что у плашки поверх игры;
страница overlay.html только рисует. Проверяем данные на RU и EN и снимаем страницу (Edge без окна) рядом
с экранной плашкой — снимки в tests/screens/obs. Программу (сервер на 127.0.0.1:8777) запускать не нужно,
но порт должен быть свободен. Запуск: python tests/test_obs.py   (из папки D:\\WardogsOverlay)"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")
sys.path.insert(0, APP)
os.chdir(APP)
import server  # noqa: E402
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


if server.already_running():
    print("Порт 8777 занят (запущена программа с плашкой для OBS?) — закрой её и запусти тест снова")
    sys.exit(2)
out = os.path.join(HERE, "screens", "obs")
os.makedirs(out, exist_ok=True)
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
settings = dict(wo.DEFAULT_SETTINGS, gunsmith=True, gunsmith_mode=0, detail=False)
server.VIEW["make"] = lambda st: wo.plate_model(st, settings)
server.start(background=True)
time.sleep(0.5)


def state(demo):
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{server.PORT}/state?demo={demo}", timeout=10).read())


def flat(v):
    return json.dumps(v, ensure_ascii=False)


def shot(name, demo):
    if not os.path.exists(EDGE):
        return
    path = os.path.join(out, name + ".png")
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=440,1000",
                    "--default-background-color=1a1a1aff", "--virtual-time-budget=4000", f"--screenshot={path}",
                    f"http://127.0.0.1:{server.PORT}/overlay.html?demo={demo}"],
                   capture_output=True, timeout=60)
    check(f"{name}: снимок есть", os.path.exists(path))


# экранная плашка с теми же примерами — для сравнения на глаз (до английского: таблицы он подменяет)
try:
    from PIL import ImageGrab
except Exception:
    ImageGrab = None
if ImageGrab:
    wo.save_settings = lambda c: None
    win = wo.OverlayWindow(dict(settings, x=40, y=40))
    for demo in ("1", "saved", "ammo", "shotgun"):
        st = dict(server.DEMOS[demo])
        st["ttk"] = wo.ttk_payload(st["weapon"], st.get("tab"), st.get("ammo"))
        win.last = None
        win.render(st)
        win.root.update()
        time.sleep(0.3)
        win.root.update()
        x, y = win.root.winfo_rootx(), win.root.winfo_rooty()
        ImageGrab.grab((x, y, x + win.W, y + win.canvas.winfo_height()), all_screens=True).save(
            os.path.join(out, f"screen_ru_{demo}.png"))
    win.root.destroy()
cyr = re.compile("[А-Яа-яЁё]")
for lang in ("ru", "en"):
    wo.LANG["code"] = lang
    wo.apply_language()
    v = state("1")["view"]
    check(f"{lang}: оружейник в OBS", v["smith"]["on"] and v["smith"]["advice"], v["smith"])
    check(f"{lang}: колонки СОВЕТ/СЕЙЧАС", v["stats"]["kind"] == "pair", v["stats"]["kind"])
    check(f"{lang}: 4 главных стата без «Подробно»", len(v["stats"]["rows"]) == 4, len(v["stats"]["rows"]))
    check(f"{lang}: TTK держит урон", v["ttk"]["hold"] and not v["ttk"]["buttons"], v["ttk"].get("hold"))
    v = state("ammo")["view"]
    check(f"{lang}: Super-45 — кнопки дистанции", v["ttk"]["buttons"] == [50, 75, 100] and v["ttk"]["dist"] == 50, v["ttk"].get("buttons"))
    v = state("shotgun")["view"]
    check(f"{lang}: дробовик — картечь и «нет данных»",
          ("Картечь" if lang == "ru" else "Buckshot") in flat(v) and v["ttk"].get("note"), flat(v)[:300])
    v = state("saved")["view"]
    check(f"{lang}: сравнение сборок — БЫЛО/СЕЙЧАС", v["stats"]["kind"] == "compare", v["stats"]["kind"])
    check(f"{lang}: в сравнении есть оружейник", v["smith"]["on"])
    if lang == "en":
        for demo in ("1", "saved", "ammo", "shotgun"):
            rus = [t for t in cyr.findall(flat(state(demo)["view"]))]
            check(f"en {demo}: нет русских букв", not rus, flat(state(demo)["view"])[:200])
    for demo in ("1", "saved", "ammo", "shotgun"):
        shot(f"obs_{lang}_{demo}", demo)
    settings["detail"] = True
    v = state("1")["view"]
    check(f"{lang}: «Подробно» — больше статов", v["detail"] and len(v["stats"]["rows"]) > 4, len(v["stats"]["rows"]))
    settings["detail"], settings["gunsmith"] = False, False
    v = state("1")["view"]
    check(f"{lang}: оружейник выключен — одна колонка", v["stats"]["kind"] == "plain" and not v["smith"]["on"])
    settings["gunsmith"] = True
    wo.LANG["code"] = "ru"

print(f"Плашка OBS: {ok} проверок прошло, ошибок {fail}")
sys.exit(1 if fail else 0)
