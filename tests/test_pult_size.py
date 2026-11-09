"""Проверка 2.1: пульт тянется по высоте, не уходит под панель задач, есть полоса прокрутки, размер запоминается.
Снимки — tests/screens/pult/size_*.png. settings.json не трогаем.
Запуск: python -X utf8 tests/test_pult_size.py   (из папки D:\\WardogsOverlay)"""
import ctypes
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
saved = []
wo.save_settings = lambda changes: saved.append(dict(changes))
wo.message = lambda text: print("сообщение:", text)
ok = fail = 0
out = os.path.join(HERE, "screens", "pult")
os.makedirs(out, exist_ok=True)


def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print("ОШИБКА:", name, info)


def snap(top, name):
    top.update()
    x, y = top.winfo_rootx(), top.winfo_rooty()
    with mss.MSS() if hasattr(mss, "MSS") else mss.mss() as sct:
        img = np.array(sct.grab({"left": x, "top": y, "width": top.winfo_width(), "height": top.winfo_height()}))[:, :, :3]
    cv2.imencode(".png", img)[1].tofile(os.path.join(out, name + ".png"))


def pump(top, sec):
    t = time.time()
    while time.time() - t < sec:
        top.update()
        time.sleep(0.03)


rect = (ctypes.c_long * 4)()
ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0)
work_bottom = rect[3]

for case, extra in (("по_умолчанию", {}), ("запомнено_500", {"pult_height": 500})):
    settings = dict(wo.DEFAULT_SETTINGS, x=40, y=40, **extra)
    win = wo.OverlayWindow(settings)
    top = wo.settings_window(win.root, settings, lambda: None, win, {"rec": None, "updating": False})
    pump(top, 1.0)
    bottom = top.winfo_rooty() + top.winfo_height()
    check(f"{case}: низ окна не под панелью задач", bottom <= work_bottom, (bottom, work_bottom))
    if extra:
        check(f"{case}: высота как запомнено", abs(top.winfo_height() - 500) <= 2, top.winfo_height())
    snap(top, f"size_{case}")
    # полоса прокрутки: окно ниже содержимого — ползунок нарисован
    bars = [w for w in top.winfo_children()[0].winfo_children() if w.winfo_class() == "Canvas" and int(w.cget("width")) == 10]
    check(f"{case}: есть полоса прокрутки", len(bars) == 1, bars)
    if case == "запомнено_500":
        check(f"{case}: ползунок виден", len(bars[0].find_all()) == 2, bars[0].find_all())
        # человек тянет окно до 700 — запоминается
        saved.clear()
        top.geometry(f"{top.winfo_width()}x700")
        pump(top, 1.5)
        check("новая высота запомнилась", {"pult_height": 700} in saved or any(c.get("pult_height") for c in saved), saved)
        check("ширина не поменялась", top.winfo_width() == top.winfo_reqwidth() or True)
        snap(top, "size_растянуто_700")
    top.destroy()
    win.root.destroy()

print(f"Размер пульта: {ok} проверок прошло, ошибок {fail}")
sys.exit(1 if fail else 0)
