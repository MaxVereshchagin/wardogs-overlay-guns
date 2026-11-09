"""Проверка 2.1: чистка старых папок _MEI (мусор однофайлового .exe до 2.1).
Во временной папке теста создаём: нашу старую папку (удалить), чужую папку PyInstaller (не трогать),
нашу папку с открытым файлом — как у запущенной старой версии (не трогать). Настоящий %TEMP% не трогаем.
Запуск: python -X utf8 tests/test_clean_mei.py   (из папки D:\\WardogsOverlay)"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")
sys.path.insert(0, APP)
os.chdir(APP)
import wardogs_overlay as wo  # noqa: E402

wo.setup_log("overlay_test.log", console_log=False)
ok = fail = 0


def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print("ОШИБКА:", name, info)


def make(base, name, ours, big=0):
    p = os.path.join(base, name)
    os.makedirs(os.path.join(p, "cv2"))
    if ours:
        open(os.path.join(p, "overlay.html"), "w").write("x")
        open(os.path.join(p, "ref_moduli.png"), "wb").write(b"x")
    open(os.path.join(p, "cv2", "big.bin"), "wb").write(b"0" * big)
    return p


with tempfile.TemporaryDirectory() as tmp:
    old = make(tmp, "_MEI11111", True, 5 * 1024 * 1024)
    alien = make(tmp, "_MEI22222", False)
    busy = make(tmp, "_MEI33333", True)
    other = os.path.join(tmp, "не_MEI")
    os.makedirs(other)
    handle = open(os.path.join(busy, "cv2", "big.bin"), "rb")   # «запущенная старая версия»
    os.environ["TEMP"] = tmp
    wo.clean_old_mei()
    handle.close()
    check("наша старая папка удалена", not os.path.exists(old) and not os.path.exists(old + "_del"))
    check("чужая папка PyInstaller на месте", os.path.isdir(alien))
    check("занятая папка на месте", os.path.isdir(busy) and os.path.isfile(os.path.join(busy, "overlay.html")))
    check("прочие папки на месте", os.path.isdir(other))
    print(sorted(os.listdir(tmp)))

print(f"Итого: {ok} ок, {fail} ошибок")
sys.exit(1 if fail else 0)
