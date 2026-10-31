"""Проверка вкладки «Боеприпасы» на снимках Макса (tests/screens/ammo) и что «Модули» не путаются с ней.
Запуск: python\\python.exe -I tests\\test_ammo.py   (журнал — tests\\test_ammo.log)"""
import glob
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
import wardogs_overlay as wo  # noqa: E402

wo.log.setLevel(logging.DEBUG)
fh = logging.FileHandler(os.path.join(HERE, "test_ammo.log"), "w", encoding="utf-8")
fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
wo.log.addHandler(fh)

# снимок → (пушка, магазин или None, патроны или None). Снимки идут по порядку, как в игре:
# на tcx_04 подсказка закрывает коробку — должны остаться AP с прошлого снимка.
AMMO = {
    "galil_01_empty": ("Galil", None, None),
    "galil_02_mag": ("Galil", "Galil 35 RND Magazine", None),
    "galil_03_ap_ready": ("Galil", "Galil 35 RND Magazine", "ArmorPiercing"),
    "galil_04_ap_ready_mag2": ("Galil", "Galil 50 RND Magazine", "ArmorPiercing"),
    "galil_05_hp_ready": ("Galil", "Galil 50 RND Magazine", "HollowPoint"),
    "m4_01_empty": ("M4", None, None),
    "m4_02_mag": ("M4", "STANAG 30 RND Magazine", None),
    "m4_03_ap_ready": ("M4", "STANAG 30 RND Magazine", "ArmorPiercing"),
    "m4_04_ap_hp_tooltip": ("M4", "STANAG 30 RND Magazine", "ArmorPiercing"),
    "m4_05_ap_ready_mag2": ("M4", "STANAG 60 RND Magazine", "ArmorPiercing"),
    "m4_06_ap_ready_mag3": ("M4", "STANAG 20 RND Magazine", "ArmorPiercing"),
    "tcx_01_mag": ("TCX-SPR", "TCX-SPR 28 RND Magazine", None),
    "tcx_02_mag_ammo_list": ("TCX-SPR", "TCX-SPR 28 RND Magazine", None),
    "tcx_03_ap_ready": ("TCX-SPR", "TCX-SPR 28 RND Magazine", "ArmorPiercing"),
    "tcx_04_ap_tooltip": ("TCX-SPR", "TCX-SPR 28 RND Magazine", "ArmorPiercing"),
    "tcx_05_ap_hp_tooltip": ("TCX-SPR", "TCX-SPR 28 RND Magazine", "ArmorPiercing"),
    "tcx_06_ap_fmj_tooltip": ("TCX-SPR", "TCX-SPR 28 RND Magazine", "ArmorPiercing"),
}
SCREENS = os.path.join(HERE, "screens")
rec = wo.Recognizer(use_disk_cache=False)
loc = wo.Locator()
ok = bad = 0


def run(path):
    img = wo.read_rgb(path)
    hit = loc.search(wo._gray(img), wo.default_scales(*img.shape[:2]), img)
    if not hit:
        return None, None
    return loc.mode, rec.analyze(wo.to_reference(img, *hit), native=(img,) + hit, mode=loc.mode)


for name, (weapon, mag, ammo) in AMMO.items():
    mode, res = run(os.path.join(SCREENS, "ammo", name + ".webp"))
    got = (mode, res and res.get("weapon"), (res or {}).get("attachments", [None])[0] if res and res.get("attachments")
           else None, res and res.get("ammo"))
    want = ("ammo", weapon, mag, ammo)
    good = got == want
    ok, bad = ok + good, bad + (not good)
    print(f"{'OK ' if good else 'ОШИБКА'} {name}: {got}" + ("" if good else f"  (ждал {want})"))

# вкладка «Модули» должна по-прежнему находиться как «Модули»
for path in sorted(glob.glob(os.path.join(SCREENS, "hover", "ak_4*.webp"))) + [os.path.join(SCREENS, "1.webp")]:
    if not os.path.exists(path):
        continue
    mode, res = run(path)
    good = mode == "modules" and res and res.get("weapon") and not res.get("tab")
    ok, bad = ok + bool(good), bad + (not good)
    print(f"{'OK ' if good else 'ОШИБКА'} {os.path.basename(path)} (модули): {mode} {res}")
# вкладка «Боеприпасы» из старых снимков (hover_34/35 — M4 без магазина)
for n in ("hover_34", "hover_35"):
    mode, res = run(os.path.join(SCREENS, "hover", n + ".webp"))
    good = mode == "ammo" and res and res.get("weapon") == "M4"
    ok, bad = ok + bool(good), bad + (not good)
    print(f"{'OK ' if good else 'ОШИБКА'} {n} (патроны): {mode} {res}")
print(f"Итог: OK {ok}, ошибок {bad}")
sys.exit(1 if bad else 0)
