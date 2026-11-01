"""Проверка «Пульта» (окно настроек 1.8): открывается на RU и EN, в английском нет русских букв,
переключатели меняют настройки сразу (оружейник, «Подробно», непрозрачность), то, что требует перезапуска,
показывает полосу «Перезапустить». Снимки — tests/screens/pult/*.png. settings.json тест не трогает.
Запуск: python tests/test_pult.py   (из папки D:\\WardogsOverlay)"""
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")
sys.path.insert(0, APP)
os.chdir(APP)
import wardogs_overlay as wo  # noqa: E402

wo.load_db()
ok = fail = 0
saved = []
wo.save_settings = lambda changes: saved.append(dict(changes))
wo.message = lambda text: print("сообщение:", text)


def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print("ОШИБКА:", name, info)


try:
    from PIL import ImageGrab
except Exception:
    ImageGrab = None
out = os.path.join(HERE, "screens", "pult")
os.makedirs(out, exist_ok=True)
cyr = re.compile("[А-Яа-яЁё]")


def texts(w):
    """Все надписи окна (метки, поля, рисунки на холстах)."""
    res = []
    for ch in w.winfo_children():
        cls = ch.winfo_class()
        if cls == "Label":
            res.append(ch.cget("text"))
        elif cls == "Canvas":
            res += [ch.itemcget(i, "text") for i in ch.find_all() if ch.type(i) == "text"]
        elif cls in ("TCombobox", "Entry"):
            res.append(ch.get())
        res += texts(ch)
    return [t for t in res if t]


def find_label(w, text):
    for ch in w.winfo_children():
        if ch.winfo_class() == "Label" and ch.cget("text") == text:
            return ch
        f = find_label(ch, text)
        if f:
            return f
    return None


wo.STATUS.update(game=True, menu=True, shown={"weapon": "M4", "tab": "modules"})
wo.SCAN["active"].set()
ctx = {"rec": object(), "updating": False}
for lang in ("ru", "en"):
    wo.LANG["code"] = lang
    wo.apply_language()
    settings = dict(wo.DEFAULT_SETTINGS, x=40, y=40, gunsmith=True)
    win = wo.OverlayWindow(settings)
    restarted = []
    top = wo.settings_window(win.root, settings, lambda: restarted.append(1), win, ctx)
    top.update()
    time.sleep(0.8)
    top.update()
    all_text = texts(top)
    if lang == "en":
        rus = [t for t in all_text if cyr.search(t) and t not in ("Русский",)]
        check("EN: нет русских букв", not rus, rus)
    else:
        check("RU: есть плитки и карточки", all(any(x in t for t in all_text) for x in ("ПОИСК МЕНЮ", "ПЛАШКА", "КЛАВИШИ В ИГРЕ", "РАЗНОЕ")),
              all_text[:20])
    check(f"{lang}: окно влезает в 1080 по высоте", top.winfo_height() <= 990, top.winfo_height())
    check(f"{lang}: плитка игры «найдена»", any(t in ("Найдена", "Found") for t in all_text))
    check(f"{lang}: плитка плашки M4", any("M4" in t for t in all_text))
    check(f"{lang}: Telegram внизу", "Telegram" in all_text)
    check(f"{lang}: «Ваша поддержка» внизу", any(t in ("Ваша поддержка", "Support us") for t in all_text))
    if ImageGrab:
        x, y = top.winfo_rootx(), top.winfo_rooty()
        ImageGrab.grab((x, y, x + top.winfo_width(), y + top.winfo_height()), all_screens=True).save(
            os.path.join(out, f"pult_{lang}.png"))
    if lang == "ru":
        # «Подробно» — переключатель рядом с подписью; щёлкаем по холсту-переключателю в той же строке
        lbl = find_label(top, "Подробно")
        row = lbl.master.master
        sw = [c for c in row.winfo_children() if c.winfo_class() == "Canvas"][0]
        before = settings["detail"]
        sw.event_generate("<Button-1>", x=10, y=10)
        top.update()
        check("«Подробно» меняется сразу", settings["detail"] != before and saved[-1] == {"detail": not before}, saved[-1:])
        check("перезапуск не нужен для «Подробно»", not restarted)
        lbl = find_label(top, "Плашка для OBS")
        sw = [c for c in lbl.master.master.winfo_children() if c.winfo_class() == "Canvas"][0]
        sw.event_generate("<Button-1>", x=10, y=10)
        top.update()
        time.sleep(0.8)
        top.update()
        check("OBS включён и сохранён", settings["obs_server"] is True and saved[-1] == {"obs_server": True}, saved[-1:])
        check("появилась полоса «Перезапустить»", find_label(top, "Перезапустить") is not None
              and find_label(top, "Перезапустить").winfo_ismapped())
        if ImageGrab:
            x, y = top.winfo_rootx(), top.winfo_rooty()
            ImageGrab.grab((x, y, x + top.winfo_width(), y + top.winfo_height()), all_screens=True).save(
                os.path.join(out, "pult_ru_obs_restart.png"))
        # смена клавиш: нажимаем по-настоящему (через Windows) — от раскладки не зависит, Esc отменяет
        import ctypes
        kb = ctypes.windll.user32.keybd_event

        def press(*vks):
            for v in vks:
                kb(v, 0, 0, 0)
                time.sleep(0.03)
            for v in reversed(vks):
                kb(v, 0, 2, 0)
                time.sleep(0.03)
            for _ in range(10):
                top.update()
                time.sleep(0.03)

        def tile_canvas(title):
            f = find_label(top, title).master.master
            return [c for c in f.winfo_children() if c.winfo_class() == "Canvas"][0]

        def canvas_text(cv):
            return " ".join(cv.itemcget(i, "text") for i in cv.find_all() if cv.type(i) == "text")

        # нажатия идут в активное окно Windows: если пульт теста не впереди (за компьютером кто-то работает) —
        # не жмём, чтобы не нажать клавиши в чужой программе
        u32 = ctypes.windll.user32
        hwnd = u32.GetParent(top.winfo_id())
        for _ in range(10):
            u32.SetForegroundWindow(hwnd)
            top.focus_force()
            top.update()
            time.sleep(0.1)
            if u32.GetForegroundWindow() == hwnd:
                break
        if u32.GetForegroundWindow() != hwnd:
            print("ПРОПУСК: окно теста не впереди — настоящие нажатия в пульте не проверял, "
                  "одиночные клавиши проверяю событием прямо в окно")
            cv = tile_canvas("Поиск меню")
            cv.event_generate("<Button-1>", x=5, y=5)
            top.update()
            top.event_generate("<KeyPress>", keycode=0x4B)       # K одна — нельзя
            top.update()
            check("K одна — подсказка в плитке", "одну нельзя" in canvas_text(cv), canvas_text(cv))
            top.event_generate("<KeyPress>", keycode=0x77)       # F8 одна — можно
            top.update()
            check("одна F8 сохранилась", settings["hotkey"] == "F8", settings["hotkey"])
            check("под одной клавишей поиска — предупреждение", "работать не будет" in canvas_text(cv), canvas_text(cv))
            cv2_ = tile_canvas("Сравнить сборку")
            cv2_.event_generate("<Button-1>", x=5, y=5)
            top.update()
            top.event_generate("<KeyPress>", keycode=0x22)       # PageDown одна — можно, без предупреждения
            top.update()
            check("PageDown для сравнения", settings["compare_hotkey"] == "PageDown" and "не будет" not in canvas_text(cv2_),
                  (settings["compare_hotkey"], canvas_text(cv2_)))
        else:
            cv = tile_canvas("Поиск меню")
            cv.event_generate("<Button-1>", x=5, y=5)
            top.focus_force()
            top.update()
            time.sleep(0.3)
            press(0x4B)                                   # просто K — без Ctrl/Shift не подходит, ждём дальше
            check("K без Ctrl — подсказка в плитке", "одну нельзя" in canvas_text(cv), canvas_text(cv))
            press(0x11, 0x10, 0x4B)                       # Ctrl+Shift+K
            check("Ctrl+Shift+K сохранилось", settings["hotkey"] == "Ctrl+Shift+K", settings["hotkey"])
            check("плитка показывает новые клавиши", "K" in canvas_text(cv) and "Esc" not in canvas_text(cv), canvas_text(cv))
            cv.event_generate("<Button-1>", x=5, y=5)
            top.focus_force()
            top.update()
            press(0x77)                                   # одна F8 — можно
            check("одна F8 сохранилась", settings["hotkey"] == "F8", settings["hotkey"])
            check("под одной клавишей поиска — предупреждение", "работать не будет" in canvas_text(cv), canvas_text(cv))
            cv2_ = tile_canvas("Сравнить сборку")
            cv2_.event_generate("<Button-1>", x=5, y=5)
            top.focus_force()
            top.update()
            press(0x1B)                                   # Esc — отмена
            check("Esc отменил ввод", "Esc" not in canvas_text(cv2_) and settings["compare_hotkey"] == "Ctrl+Shift+S",
                  canvas_text(cv2_))
            check("Esc не закрыл пульт во время ввода", top.winfo_exists())
        if ImageGrab:
            x, y = top.winfo_rootx(), top.winfo_rooty()
            ImageGrab.grab((x, y, x + top.winfo_width(), y + top.winfo_height()), all_screens=True).save(
                os.path.join(out, "pult_ru_keys.png"))
        find_label(top, "Перезапустить").event_generate("<Button-1>")
        check("кнопка «Перезапустить» перезапускает", restarted == [1])
    win.root.destroy()
# какие клавиши можно назначить
wo.LANG["code"] = "ru"
for text, good in (("Ctrl+Shift+O", True), ("F8", True), ("Num5", True), ("PageUp", True), ("Insert", True),
                   ("O", False), ("7", False), ("Win+O", False), ("Alt+7", True)):
    try:
        wo.parse_hotkey(text)
        got = True
    except ValueError:
        got = False
    check(f"клавиша {text}: {'можно' if good else 'нельзя'}", got == good)

# одиночная F-клавиша по-настоящему работает: поиск (занята в Windows) и сравнение (подслушивается)
import ctypes
import tkinter as tk
kb = ctypes.windll.user32.keybd_event
r = tk.Tk()
r.withdraw()


def tap(vk):
    kb(vk, 0, 0, 0)
    time.sleep(0.08)
    kb(vk, 0, 2, 0)
    time.sleep(0.4)


wo.SCAN["active"].set()
wo.SCAN["hotkey"] = "F8"
wo.listen_hotkey("F8")
time.sleep(0.5)
tap(0x77)
check("F8 выключила поиск", not wo.SCAN["active"].is_set())
tap(0x77)
check("F8 снова включила поиск", wo.SCAN["active"].is_set())
wo.STATUS["shown"] = {"weapon": "M4", "attachments": ["x"], "tab": "modules"}
wo.SAVED.clear()
wo.listen_compare_key("F9")
time.sleep(0.3)
tap(0x78)
check("F9 запомнила сборку", "M4" in wo.SAVED, wo.SAVED)
r.destroy()
print(f"Пульт: {ok} проверок прошло, ошибок {fail}")
sys.exit(1 if fail else 0)
