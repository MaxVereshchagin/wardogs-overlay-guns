"""2.1.1: пульт не обрезается справа (скрин подписчика 09.11). Открываем пульт и СРАЗУ (без ожидания событий) меряем:
правый край каждого элемента внутри окна. Плюс «крупные шрифты» (масштаб Tk как при 150 % без исправления).
Запуск: python -X utf8 tests/test_pult_width.py [путь к другой версии wardogs_overlay.py — для сравнения]"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")
sys.path.insert(0, APP)
os.chdir(APP)
if len(sys.argv) > 1:
    import importlib.util
    spec = importlib.util.spec_from_file_location("wardogs_overlay", sys.argv[1])
    wo = importlib.util.module_from_spec(spec)
    sys.modules["wardogs_overlay"] = wo
    spec.loader.exec_module(wo)
else:
    import wardogs_overlay as wo  # noqa: E402

wo.setup_log("overlay_test.log", console_log=False)
wo.load_db()
wo.save_settings = lambda c: None
wo.message = lambda t: None
ok = fail = 0


def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print("ОШИБКА:", name, info)


def right_edge(top):
    m = 0
    stack = [top]
    while stack:
        w = stack.pop()
        stack.extend(w.winfo_children())
        if w is top or not w.winfo_ismapped():
            continue
        if w.winfo_class() in ("Label", "Canvas", "Spinbox", "Entry", "TCombobox"):
            m = max(m, w.winfo_rootx() + w.winfo_reqwidth() - top.winfo_rootx())
    return m


for lang in ("ru", "en"):
    for scaling in (None, 2.0):
        wo.LANG["code"] = lang
        wo.apply_language()
        settings = dict(wo.DEFAULT_SETTINGS, x=40, y=40)
        win = wo.OverlayWindow(settings)
        if scaling:
            win.root.tk.call("tk", "scaling", scaling)    # шрифты крупнее — как на ПК, где пульт шире
        top = wo.settings_window(win.root, settings, lambda: None, win, {"rec": None, "updating": False})
        top.update()                                     # один раз, без долгого ожидания
        edge, width = right_edge(top), top.winfo_width()
        check(f"{lang}, масштаб {scaling or 'обычный'}: ничего не вылезает справа", edge <= width, (edge, width))
        print(f"{lang}, масштаб {scaling or 'обычный'}: правый край содержимого {edge}, окно {width}")
        top.destroy()
        win.root.destroy()
print(f"Ширина пульта: {ok} проверок прошло, ошибок {fail}")
sys.exit(1 if fail else 0)
