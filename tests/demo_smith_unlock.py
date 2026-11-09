"""2.1: тестовые картинки плашки — «где открывается» под советом и «лучший — …, откроется…» при включённых уровнях.
Снимки — tests/screens/smith_unlock/*.png.  Запуск: python -X utf8 tests/demo_smith_unlock.py [ru|en]"""
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
lang = sys.argv[1] if len(sys.argv) > 1 else "ru"
wo.LANG["code"] = lang
wo.apply_language()
out = os.path.join(HERE, "screens", "smith_unlock")
os.makedirs(out, exist_ok=True)
settings = dict(wo.DEFAULT_SETTINGS, x=40, y=40, gunsmith=True)
win = wo.OverlayWindow(settings)
low = {"Career": 5, "Infantry": 8, "Medic": 5, "Recon": 5, "Support": 5}
over = 0
for name, weapon, atts, upd in (("1_AK74_без_уровней", "AK74", [], {"levels": None}),
                                ("2_AK74_уровни_8", "AK74", [], {"levels": low}),
                                ("3_M4_уровни_8_стоит_другое", "M4", ["Birdcage Flash Hider"], {"levels": low}),
                                ("4_T-21_уровни_8", "T-21", [], {"levels": low})):
    settings.update(upd)
    win.last = None
    win.render({"weapon": weapon, "attachments": atts, "tab": "modules", "ttk": True})
    win.root.update()
    time.sleep(0.4)
    win.root.update()
    c = win.canvas
    right = max(c.bbox(i)[2] for i in c.find_all() if c.type(i) == "text")
    if right > win.W + 1:
        over += 1
        print("ВЫЛЕЗАЕТ:", name, right)
    x, y = win.root.winfo_rootx(), win.root.winfo_rooty()
    with mss.mss() as sct:
        img = np.array(sct.grab({"left": x, "top": y, "width": win.root.winfo_width(), "height": win.root.winfo_height()}))[:, :, :3]
    cv2.imencode(".png", img)[1].tofile(os.path.join(out, f"{lang}_{name}.png"))
win.root.destroy()
print("готово, вылезает:", over)
