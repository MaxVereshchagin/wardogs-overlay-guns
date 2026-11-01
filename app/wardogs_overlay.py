"""Wardogs: бафы и дебафы обвесов поверх игры.

Программа раз в секунду смотрит на экран. Когда открыто меню продавца → «Модули»,
она узнаёт пушку (по названию), обвесы (по иконкам) и магазин (по цифре «30/30»)
и показывает плашку с плюсами и минусами:
  • в окне поверх игры (игра должна быть в режиме «Оконный без рамки»);
  • для OBS (по желанию, "obs_server": true): источник «Браузер» → http://127.0.0.1:8777/overlay.html

Запуск:  «Wardogs Overlay.bat» (окно с меню: вкл/выкл поиск, обновить базу, настройки, журнал, выход)
Журнал:  overlay.log.  Настройки:  settings.json.
Проверка на скриншотах:  python wardogs_overlay.py --test 1.png 2.png
"""
import ctypes
import difflib
import hashlib
import json
import logging
import logging.handlers
import math
import os
import platform
import re
import subprocess
import sys
import threading
import time

import cv2
import numpy as np

# Обычный запуск: всё лежит в папке app рядом с этим файлом.
# WardogsOverlay.exe (PyInstaller): код и образцы надписей распакованы во временную папку (BUNDLE),
# а база, иконки, настройки, память и журналы — рядом с самим .exe (HERE), чтобы их можно было менять.
FROZEN = getattr(sys, "frozen", False)
HERE = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
BUNDLE = getattr(sys, "_MEIPASS", HERE)
sys.path.insert(0, HERE)
if FROZEN and "--update-db" in sys.argv:      # .exe сам себе «обновить базу» (вместо python update_database.py)
    import update_database
    sys.exit(update_database.run())
if FROZEN and len(sys.argv) == 1:              # двойной щелчок по .exe — обычный запуск со значком у часов
    sys.argv.append("--tray")
import server  # noqa: E402

VERSION = "2.0"
LANG = {"code": "ru"}     # язык интерфейса для игрока: "ru" / "en" (настройка "language": auto / ru / en)


def T(ru, en):
    """Надпись для игрока на выбранном языке. Журнал overlay.log всегда по-русски."""
    return en if LANG["code"] == "en" else ru


def pick_language(setting):
    """"auto" — как язык Windows (русский/украинский/белорусский/казахский → ru, иначе en)."""
    s = str(setting or "auto").lower()
    if s in ("ru", "en"):
        return s
    try:
        lid = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
        return "ru" if lid in (0x19, 0x22, 0x23, 0x3F) else "en"
    except Exception:
        return "ru"
GUIDE_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id=3822235930"   # руководство: там новые версии
GUIDE_URL_EN = "https://steamcommunity.com/sharedfiles/filedetails/?id=3831395119"   # английское руководство (2.0)


def guide_url():
    """Какое руководство открывать человеку: английское — в английском режиме. Новые версии ищем всегда
    в русском (там строка «Обновление X.Y»)."""
    return T(GUIDE_URL, GUIDE_URL_EN)
ICON_FILE = os.path.join(BUNDLE, "overlay.ico")


def updater_cmd():
    """Как запустить обновление базы отдельным процессом."""
    if FROZEN:
        return [sys.executable, "--update-db"]
    return [sys.executable, "-I", os.path.join(HERE, "update_database.py")]

# Подробный журнал работы: overlay.log рядом с программой (проверка на скриншотах — overlay_test.log).
# Пишется всегда; больше 5 МБ — старый переименовывается в overlay.log.1.
log = logging.getLogger("wardogs")
LOG_MAX_BYTES = 5 * 1024 * 1024

# Картинки читаем и пишем через OpenCV (Pillow не нужен — минус 15 МБ).
# Через np.fromfile/imdecode, а не cv2.imread — иначе не открываются пути с русскими буквами.
def read_rgb(path):
    img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"не открывается картинка {path}")
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def read_rgba(path):
    img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"не открывается картинка {path}")
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2RGBA)
    if img.shape[2] == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGBA)
    return cv2.cvtColor(img, cv2.COLOR_BGRA2RGBA)


def save_jpg(path, rgb, quality=85):
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality])
    if ok:
        buf.tofile(path)


# Журнал событий понятными словами: «события.txt» рядом с программой ([4] в меню открывает его).
# Подробный технический журнал — overlay.log (для разбора проблем).
EVENTS_FILE = os.path.join(HERE, "события.txt")
EVENTS_MAX_BYTES = 1024 * 1024        # больше 1 МБ — оставляем последние 2000 строк
EVENTS_CHANGED = threading.Event()    # меню перерисовывается, когда что-то произошло
_events_lock = threading.Lock()
_last_event = {"text": None}
_EVENT_MARK = {"warn": "⚠ ", "err": "✖ "}


def describe_build(st):
    """«AK74: Four Reticle Reflex, RK6 Tactical Foregrip, PBS-4 Suppressor, магазин 30»."""
    parts = []
    for n in st.get("attachments", []):
        a = ATTS.get(n)
        if a and a[1] in ("Magazine", "Ammunition"):
            m = re.search(r"\b(\d+) RND\b", n)
            parts.append(f"{T('магазин', 'mag')} {a[4] if len(a) > 4 and a[4] else (m.group(1) if m else '')}".strip())
        else:
            parts.append(n)
    if st.get("tab") == "ammo":   # вкладка «Боеприпасы»: магазин и патроны
        rnd = {"FMJ": T("обычные", "standard"), "HollowPoint": "HP", "ArmorPiercing": "AP"}.get(
            st.get("ammo"), T("не выбраны", "not chosen"))
        return f"{st['weapon']} ({T('патроны', 'ammo')}): " + ", ".join(parts + [f"{T('патроны', 'ammo')} {rnd}"])
    return f"{st['weapon']}: " + (", ".join(parts) if parts else T("без обвесов", "no attachments"))


def events_start():
    """При запуске: подрезать слишком длинный файл событий и отметить начало сеанса."""
    try:
        if os.path.exists(EVENTS_FILE) and os.path.getsize(EVENTS_FILE) > EVENTS_MAX_BYTES:
            tail = open(EVENTS_FILE, encoding="utf-8", errors="replace").read().splitlines()[-2000:]
            open(EVENTS_FILE, "w", encoding="utf-8").write("\n".join(tail) + "\n")
        with open(EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n──────── запуск {time.strftime('%d.%m.%Y %H:%M')} ────────\n")
    except OSError:
        log.exception("Не смог открыть события.txt")


def ev(text, kind="info"):
    """Событие понятными словами → «события.txt» (и в подробный журнал)."""
    with _events_lock:
        if _last_event["text"] == text:   # одно и то же подряд не повторяем
            return
        _last_event["text"] = text
        try:
            with open(EVENTS_FILE, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%d.%m %H:%M:%S')}  {_EVENT_MARK.get(kind, '')}{text}\n")
        except OSError:
            log.exception("Не смог записать в события.txt")
    log.debug("Событие: %s", text)
    EVENTS_CHANGED.set()

# ---------------------------------------------------------------- настройки
DEFAULT_SETTINGS = {
    "x": "auto",          # где окно: "auto" — рядом с меню игры; или число (пиксели)
    "y": "auto",
    "opacity": 0.92,      # прозрачность окна 0..1
    "monitor": "auto",    # на каком мониторе игра: "auto" — найти самому; или номер 1, 2, 3…
    "interval": 0.5,      # как часто смотреть на экран, секунд
    "show_window": True,  # показывать окно поверх игры
    "obs_server": False,  # true — включить плашку для OBS (источник «Браузер» → http://127.0.0.1:8777/overlay.html)
    "match_threshold": 0.7,
    "log_level": "DEBUG",  # подробность журнала: DEBUG (всё) / INFO (главное) / WARNING (только проблемы)
    "hotkey": "Ctrl+Shift+O",  # включить/выключить поиск меню прямо в игре (Ctrl/Shift/Alt/Win + буква, цифра или F1–F12)
    "auto_off_seconds": 10,    # меню «Модули» не видно столько секунд — поиск засыпает (0 — никогда)
    "confirm_frames": 2,       # сколько кадров подряд должно совпасть, чтобы плашка поменялась
    "compare_hotkey": "Ctrl+Shift+S",  # запомнить сборку и сравнивать с ней (работает, только пока видна плашка)
    "check_updates": True,     # при запуске заглянуть в руководство Steam: не вышла ли новая версия
    "gunsmith": False,         # «Оружейник рекомендует»: лучшие обвесы и статы сборки с советом (галочка на плашке)
    "gunsmith_mode": 0,        # что считать лучшим: 0 — баланс, 1 — меньше отдачи, 2 — быстрое ADS (кнопки на плашке)
    "detail": False,           # «Подробно»: все меняющиеся статы; иначе только 4 главных (галочка в углу плашки)
    "ttk_dist": {},            # выбранная кнопкой дистанция TTK по пушкам, м (у кого урон падает: ПП, пистолеты…)
    "language": "auto",        # язык программы: "auto" — как Windows; "ru" — русский; "en" — English
}


def save_settings(changes):
    """Записать изменённые настройки в settings.json (остальное, в том числе незнакомое, — как было)."""
    path = os.path.join(HERE, "settings.json")
    s = dict(DEFAULT_SETTINGS)
    if os.path.exists(path):
        try:
            s.update(json.load(open(path, encoding="utf-8-sig")))
        except Exception as e:
            log.error("settings.json не читается — запишу заново: %s", e)
    s.update(changes)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    log.info("Настройки сохранены: %s", json.dumps(changes, ensure_ascii=False))


def load_settings():
    path = os.path.join(HERE, "settings.json")
    s = dict(DEFAULT_SETTINGS)
    if os.path.exists(path):
        try:
            s.update(json.load(open(path, encoding="utf-8-sig")))
        except Exception as e:
            log.error("settings.json не читается, беру настройки по умолчанию: %s", e)
    else:
        json.dump(s, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return s


# ---------------------------------------------------------------- база
def ensure_database():
    """Первый запуск (например, скачано с GitHub): базы и иконок ещё нет — скачиваем их с MetaForge сами.
    Статы и иконки — данные сайта MetaForge и картинки из игры, поэтому в репозиторий их не кладём."""
    data = os.path.join(HERE, "wardogs_data.js")
    icons_json = os.path.join(HERE, "icons.json")
    icons_dir = os.path.join(HERE, "icons")
    have_icons = os.path.isdir(icons_dir) and any(f.endswith(".webp") for f in os.listdir(icons_dir))
    if os.path.exists(data) and os.path.exists(icons_json) and have_icons:
        return
    msg = "\n Первый запуск: скачиваю базу пушек, обвесов и иконки с MetaForge (несколько секунд)…\n"
    try:
        if sys.stdout is not None:
            print(msg, flush=True)
    except Exception:
        pass
    r = subprocess.run(updater_cmd(), cwd=HERE,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not os.path.exists(data):
        text = T("Не получилось скачать базу с MetaForge — нужен интернет при первом запуске.\n\n"
                  "Подробности — в app\\update_database.log. Проверь интернет и запусти снова.",
                  "Could not download the database from MetaForge — the first launch needs internet.\n\n"
                  "Details are in app\\update_database.log. Check your connection and start again.")
        try:
            ctypes.windll.user32.MessageBoxW(None, text, "Wardogs оверлей", 0x10)
        finally:
            sys.exit(1)


ensure_database()


def load_db():
    js = open(os.path.join(HERE, "wardogs_data.js"), encoding="utf-8-sig").read()
    i = js.find("window.WARDOGS")
    try:   # формат update_database.py: window.WARDOGS = {...json...};
        data = json.loads(js[js.index("{", i): js.rindex("}") + 1])
        log.debug("База: данные MetaForge от %s", data.get("generatedAt"))
        BALL.clear()
        BALL.update(data.get("B") or {})   # баллистика для TTK (нет в старой базе — TTK не показываем)
        if not BALL:
            log.warning("В базе нет баллистики — TTK не покажу (обнови базу)")
        return data["W"], data["A"]
    except ValueError:   # самый первый формат (W и A отдельными кусками)
        W = json.loads(re.search(r"W:(\[.*?\]\]),\s*\n", js, re.S).group(1))
        A = json.loads(re.search(r"A:(\[.*\])\s*\};", js, re.S).group(1))
        return W, A


BALL = {}   # баллистика MetaForge (для TTK), заполняет load_db
W_LIST, A_LIST = load_db()


def weapon_calibre(weapon):
    w = next((x for x in BALL.get("weapons", []) if x.get("name") == weapon), None)
    return w and w.get("calibre")


def round_icons(weapon):
    """Иконки коробок патронов этой пушки: {"FMJ": "AMMO|556mm|FMJ", …} (только те, что скачаны)."""
    cal = weapon_calibre(weapon)
    icons = ((BALL.get("calibres") or {}).get(cal or "") or {}).get("roundIcons") or {}
    return {r: f"AMMO|{cal}|{r}" for r, f in icons.items()
            if os.path.exists(os.path.join(HERE, "icons", f + ".webp"))}


def round_icon_file(name):
    _, cal, rnd = name.split("|")
    return BALL["calibres"][cal]["roundIcons"][rnd]
WEAPONS = {w[0]: w for w in W_LIST}
ATTS = {a[0]: a for a in A_LIST}
ICONS = json.load(open(os.path.join(HERE, "icons.json"), encoding="utf-8-sig"))
MAG_TYPES = ("Magazine", "Ammunition")

# Кириллица, похожая на латиницу (игра пишет «АК-74»)
_LOOKALIKE = str.maketrans("АВЕКМНОРСТХУ", "ABEKMHOPCTXY")


# Распознавание часто читает русские буквы как похожие латинские: «СКС» → «CKC», «ПКМ» → «nKM».
# Поэтому и русские названия, и прочитанный текст приводим к одному «латинскому виду».
_VISUAL = {"П": "N", "И": "N", "Й": "N", "Ю": "IO", "Л": "L", "Д": "D", "Г": "R", "Я": "R",
           "Б": "6", "З": "3", "Э": "3", "Ь": "B", "Ы": "BI", "Ц": "U", "Ш": "W", "Щ": "W",
           "Ж": "X", "Ф": "O", "Ч": "4", "Ё": "E"}


def norm(s):
    s = s.upper().translate(_LOOKALIKE)
    s = "".join(_VISUAL.get(ch, ch) for ch in s)
    return re.sub(r"[^A-Z0-9]", "", s)


# Как игра (на русском) называет пушку → как она называется в базе.
# Первые четыре видели в игре, остальные — вероятные варианты.
# Пушка не узнаётся → её название из игры появится в unknown_names.txt — дописать сюда.
ALIASES = {
    "СКС": "SKS",
    "СВ98": "SV98",
    "ПКМ": "PKM",
    "ВИНТОВКА МОСИНА": "Mosin Nagant",
    "СВД": "SVD",
    "РПГ-7": "RPG-7",
    "ПП-19 БИЗОН": "PP-19 Bizon",
    "ПП-19 ВИТЯЗЬ": "PP-19 Vityaz",
    "ВЕРБА": "9K333 Verba",
    "БЛОЧНЫЙ ЛУК": "Compound Bow",
    "SNOHHDIN IYK": "Compound Bow",   # так «БЛОЧНЫЙ ЛУК» читается на 1280×720 (снимок Макса 30.10)
}
_WEAPON_NORM = {norm(n): n for n in WEAPONS}
_WEAPON_NORM.update({norm(k): v for k, v in ALIASES.items()})


def reload_db():
    """Перечитать базу после «обновить базу» — без перезапуска программы."""
    W, A = load_db()
    WEAPONS.clear()
    WEAPONS.update({w[0]: w for w in W})
    ATTS.clear()
    ATTS.update({a[0]: a for a in A})
    ICONS.clear()
    ICONS.update(json.load(open(os.path.join(HERE, "icons.json"), encoding="utf-8-sig")))
    _WEAPON_NORM.clear()
    _WEAPON_NORM.update({norm(n): n for n in WEAPONS})
    _WEAPON_NORM.update({norm(k): v for k, v in ALIASES.items()})
    _TTK_CACHE.clear()
    log.info("База перечитана: %d пушек, %d обвесов, баллистика от %s", len(WEAPONS), len(ATTS),
             BALL.get("generatedAt"))
    return len(WEAPONS), len(ATTS)


def match_weapon(text, quiet=False):
    """Название из игры → пушка из базы. quiet — не жаловаться в журнал (будет ещё попытка прочитать)."""
    if not text:
        return None
    n = norm(text)
    if not n:
        return None
    if n in _WEAPON_NORM:
        return _WEAPON_NORM[n]
    # лишнее слово перед названием (скин и т.п.): «ШТУРМ АК-74» → AK74
    tail = [k for k in _WEAPON_NORM if len(k) >= 3 and n.endswith(k)]
    if tail:
        return _WEAPON_NORM[max(tail, key=len)]
    best = difflib.get_close_matches(n, _WEAPON_NORM.keys(), n=1, cutoff=0.75)
    if best:
        return _WEAPON_NORM[best[0]]
    if quiet:
        return None
    log.warning("Пушка не узнана: прочитано %r → %r (записал в unknown_names.txt)", text, n)
    ev(T(f"Не узнал пушку «{text}» — см. unknown_names.txt", f"Unknown weapon «{text}» — see unknown_names.txt"), "warn")
    with open(os.path.join(HERE, "unknown_names.txt"), "a", encoding="utf-8") as f:
        f.write(text + "\n")
    return None


# ---------------------------------------------------------------- распознавание
# Координаты для экрана 1920×1080 (меню продавца → Экипировка → Модули).
HEADER_POS = (140, 440)             # надпись «МОДУЛИ»
CARD_X = [155, 494, 833]            # три карточки: основное / личное / специальное
CARD_NAME = (3, 318, 262, 346)      # x0, y0, x1, y1 (x — от левого края карточки)
CARD_CAP = (281, 322, 313, 342)     # «30/30»
MODULES_BTN = (238, 377)            # центр кнопки «МОДУЛИ» под карточкой (оранжевая = выбрана)
SLOT_Y = 487
SLOT_X0, SLOT_STEP, SLOT_W, SLOT_H = 203, 116.6, 106, 92
ROW_RIGHT = 1105
ICON_SIZES = (82, 84, 86)          # квадратная иконка в ячейке ~84 px при 1080p (для версии памяти)
BADGE_X = 0.38                     # ценник над иконкой: при сравнении не смотрим правее этой доли ширины
                                   # (было 0.55 под «$0»; с деньгами ценник длиннее — «$540», «$1 250»)
MAG_ICON_SURE = 0.75              # иконке магазина верим, если похожа хотя бы так (и с отрывом 0.15)
HOLD_MIN = 0.40                  # под курсором: оставить уже узнанный обвес, если похож хотя бы так
CURSOR_BRIGHT = 110              # …и если в ячейке есть яркие точки (курсор); у пустого слота с силуэтом их нет (~88)
ICON_BOX = (96, 84)                # рамка, в которую игра вписывает иконку (ширина, высота) при 1080p
# Вкладка «Боеприпасы» (эталон 1920×1080): ячейка «ШАГ 1» — магазин, «ШАГ 2» — коробка патронов
AMMO_MAG_SLOT = (157, 477, 268, 588)
AMMO_BOX_SLOT = (594, 476, 712, 589)
AMMO_SLOT_MIN = 0.6                # иконке магазина / патронов верим, если похожа хотя бы так (и с отрывом)
AMMO_COVERED = 0.2                 # ниже — ячейка патронов пустая; выше (но не узнана) — коробку что-то закрыло
AMMO_CHOICE = {}                   # пушка → патроны, выбранные на вкладке «Боеприпасы» (до закрытия программы)



# Какие типы обвесов встают в одну ячейку
SLOT_GROUP = {"Muzzle": "muzzle", "Suppressor": "muzzle", "Foregrip": "grip", "Bipod": "grip",
              "Optic": "optic", "Magazine": "mag", "Ammunition": "mag"}


CACHE_FILE = os.path.join(HERE, "recognition_cache.json")
CACHE_VERSION = f"1|{ICON_SIZES}|{len(ICONS)}|{len(ATTS)}"  # поменялась база/иконки — память сбрасывается


def img_hash(a):
    """Отпечаток картинки: мелкий шум не мешает, разные иконки — разные отпечатки."""
    return hashlib.md5((a[::2, ::2] // 8).astype(np.uint8).tobytes()).hexdigest()


class Recognizer:
    def __init__(self, threshold=0.7, use_disk_cache=True):
        from rapidocr_onnxruntime import RapidOCR
        self.ocr = RapidOCR()
        self.threshold = threshold
        # заголовок «МОДУЛИ» (русская игра) и ATTACHMENTS (английская) — стоят на одном месте
        self.header_refs = [read_rgb(os.path.join(BUNDLE, n)).astype(np.float32)
                            for n in ("ref_moduli.png", "ref_moduli_en.png") if os.path.exists(os.path.join(BUNDLE, n))]
        self._tpl_cache = {}
        self.last_top = []
        self._slot_last = {}         # (пушка, № ячейки) → какой обвес там был в последний раз (за этот запуск)
        self._hold = {}              # отпечаток ячейки → обвес, оставленный под курсором
        self._mag_icon_cache = {}    # отпечаток ячейки магазина → (магазин по иконке, точность, отрыв)
        self._ocr_session = {}       # прочитанный текст за этот запуск (и неудачные попытки тоже)
        self._miss = set()          # ячейки, где ничего не нашлось (только до закрытия программы)
        self._miss_score = {}       # …и насколько они всё же были похожи (вкладка «Боеприпасы»)
        # Память, которая сохраняется на диск:
        #   slots      — отпечаток ячейки → обвес
        #   ocr        — отпечаток надписи → текст
        #   slot_types — пушка → какой тип обвеса в какой по счёту ячейке
        self.mem = {"version": CACHE_VERSION, "slots": {}, "ocr": {}, "slot_types": {}}
        self.use_disk_cache = use_disk_cache
        self._dirty = False
        self._saved_at = 0.0
        if use_disk_cache and os.path.exists(CACHE_FILE):
            try:
                m = json.load(open(CACHE_FILE, encoding="utf-8-sig"))
                if m.get("version") == CACHE_VERSION:
                    self.mem = m
            except Exception as e:
                log.error("Память распознавания не читается, начинаю заново: %s", e)
        log.info("Память распознавания: %d ячеек, %d надписей, пушек с известными ячейками: %d",
                 len(self.mem["slots"]), len(self.mem["ocr"]), len(self.mem["slot_types"]))

    def save(self, force=False):
        if not (self.use_disk_cache and self._dirty):
            return
        if not force and time.time() - self._saved_at < 3:
            return
        tmp = CACHE_FILE + ".tmp"
        json.dump(self.mem, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
        self._dirty, self._saved_at = False, time.time()

    def remember(self, section, key, value):
        if self.mem[section].get(key) != value:
            self.mem[section][key] = value
            self._dirty = True

    # --- иконки как шаблоны (с маской: прячем ценник «$0» и метку «1»)
    def templates(self, name):
        if name in self._tpl_cache:
            return self._tpl_cache[name]
        path = os.path.join(HERE, "icons", (ICONS.get(name) or round_icon_file(name)) + ".webp")
        ic = read_rgba(path)
        # Игра вписывает иконку в рамку ~96×84 С СОХРАНЕНИЕМ пропорций: квадратные → 84×84,
        # длинные прицелы (200×144, 232×132…) → ~96×69. Сплющивать в квадрат нельзя — не узнаются.
        H, W = ic.shape[:2]
        k0 = min(ICON_BOX[0] / W, ICON_BOX[1] / H)
        out = []
        for k in (k0 * 0.976, k0, k0 * 1.024):
            w, h = int(round(W * k)), int(round(H * k))
            t = cv2.resize(ic, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32)
            al = t[..., 3:] / 255.0
            tpl = t[..., :3] * al + 30.0 * (1 - al)
            mask = np.ones((h, w), np.float32)
            mask[: int(h * 0.27), int(w * BADGE_X):] = 0   # ценник («$0» … «$1 250»)
            mask[: int(h * 0.18), : int(w * 0.3)] = 0    # метка «1»
            out.append((tpl, np.dstack([mask] * 3)))
        self._tpl_cache[name] = out
        return out

    def read_text(self, crop, scale):
        """Возвращает (текст, отпечаток). В память текст кладёт analyze — только если он осмысленный."""
        key = img_hash(crop)
        if key in self.mem["ocr"]:
            return self.mem["ocr"][key], key
        if key in self._ocr_session:   # эту же картинку уже читали (даже если не вышло) — не повторяем
            return self._ocr_session[key], key
        t = time.time()
        big = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        res, _ = self.ocr(big)
        text = " ".join(r[1] for r in res) if res else ""
        log.debug("OCR: кусок %dx%d, увеличение ×%.1f → %r (уверенность %s) за %.2f с",
                  crop.shape[1], crop.shape[0], scale, text,
                  [round(float(r[2]), 2) for r in res] if res else [], time.time() - t)
        if len(self._ocr_session) > 500:
            self._ocr_session.clear()
        self._ocr_session[key] = text
        return text, key

    def mag_icon(self, key, region, mag_cands):
        """Какой магазин по иконке: (название, точность, отрыв от второго магазина). Запоминаем по картинке."""
        if key not in self._mag_icon_cache:
            if not mag_cands:
                self._mag_icon_cache[key] = (None, 0.0, 0.0)
            else:
                s1, n1 = self.best_match(region, mag_cands)
                s2 = self.last_top[1][1] if len(self.last_top) > 1 else 0.0
                self._mag_icon_cache[key] = (n1, s1, s1 - s2)
                log.debug("Иконка магазина: %s %.3f (отрыв %.3f)", n1, s1, s1 - s2)
        return self._mag_icon_cache[key]

    def _holds(self, weapon, i, best_name, best):
        """Оставить обвес, хотя точность ниже порога? Только если это тот же обвес, что был в этой
        ячейке только что, он по-прежнему первый и с заметным отрывом от второго места."""
        if not best_name or self._slot_last.get((weapon, i)) != best_name or best < HOLD_MIN:
            return False
        second = self.last_top[1][1] if len(self.last_top) > 1 else -1.0
        return best - second >= 0.06

    def best_match(self, region, names):
        """Лучшая иконка для ячейки. Второе место запоминаем для журнала (насколько уверены)."""
        scores = {}
        for name in names:
            for tpl, mask in self.templates(name):
                if tpl.shape[0] > region.shape[0] or tpl.shape[1] > region.shape[1]:
                    continue
                r = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED, mask=mask)
                s = float(np.nan_to_num(r, nan=-1, posinf=-1, neginf=-1).max())
                if s > scores.get(name, -1.0):
                    scores[name] = s
        top = sorted(scores.items(), key=lambda kv: -kv[1])[:3]
        self.last_top = top
        return (top[0][1], top[0][0]) if top else (-1.0, None)

    def _slot_pick(self, img, box, names, what, weapon):
        """Что лежит в ячейке box (x0, y0, x1, y1 эталона): (название или None, точность). Запоминает по картинке."""
        x0, y0, x1, y1 = box
        region = img[y0 - 6: y1 + 6, x0 - 6: x1 + 6].astype(np.float32)
        if region.std() < 6 or not names:
            return None, 0.0
        key = f"{what}|{weapon}|{img_hash(region)}"
        if key in self.mem["slots"]:
            return self.mem["slots"][key], 1.0
        if key in self._miss:
            return None, self._miss_score.get(key, 0.0)
        t = time.time()
        best, name = self.best_match(region, names)
        log.debug("%s, %s: лучшие %s за %.2f с", weapon, what,
                  ", ".join(f"{n} {s:.3f}" for n, s in self.last_top), time.time() - t)
        second = self.last_top[1][1] if len(self.last_top) > 1 else 0.0
        if name and best >= AMMO_SLOT_MIN and best - second >= 0.05:
            self.remember("slots", key, name)
            log.info("%s, %s: %s (точность %.3f, второе место %.3f)", weapon, what, name, best, second)
            return name, best
        log.info("%s, %s: не узнал (лучшее %s %.3f, второе %.3f)", weapon, what, name, best, second)
        self._miss.add(key)
        self._miss_score[key] = best
        return None, best

    def analyze_ammo(self, img, weapon):
        """Вкладка «Боеприпасы»: какой магазин выбран (шаг 1) и какие патроны (шаг 2)."""
        mags = [n for n, a in ATTS.items() if weapon in a[3] and a[1] in MAG_TYPES and n in ICONS]
        mag, _ = self._slot_pick(img, AMMO_MAG_SLOT, mags, "магазин (шаг 1)", weapon)
        rounds = round_icons(weapon)
        box, score = self._slot_pick(img, AMMO_BOX_SLOT, list(rounds.values()), "патроны (шаг 2)", weapon)
        ammo = next((r for r, n in rounds.items() if n == box), None)
        if ammo:
            AMMO_CHOICE[weapon] = ammo      # TTK на вкладке «Модули» дальше считаем с этими патронами
        elif score >= AMMO_COVERED and weapon in AMMO_CHOICE:
            # коробку частично закрыла подсказка или курсор (пустая ячейка похожа ≤0.14, закрытая ~0.29) —
            # оставляем патроны, что были, иначе плашка мигает, пока водишь мышью
            ammo = AMMO_CHOICE[weapon]
            log.debug("%s: коробка патронов закрыта (%.3f) — оставляю %s", weapon, score, ammo)
        self.save()
        return {"weapon": weapon, "attachments": [mag] if mag else [], "tab": "ammo", "ammo": ammo}

    def find_header(self, img, min_score=0.8):
        """Ищем «МОДУЛИ» — значит открыто меню сборки. Возвращает сдвиг по вертикали или None."""
        x, y = HEADER_POS
        band = img[max(0, y - 40): y + 62, x - 10: x + 125].astype(np.float32)
        score, loc = max((cv2.minMaxLoc(cv2.matchTemplate(band, ref, cv2.TM_CCOEFF_NORMED))[1:4:2]
                          for ref in self.header_refs), key=lambda r: r[0])
        if score < min_score:
            return None
        return (max(0, y - 40) + loc[1]) - y

    def analyze(self, img, native=None, mode="modules"):
        """img — эталонный кадр 1920×1080. native — (исходный кадр, s, x, y надписи): оттуда читаем
        текст в родном разрешении, мелкие цифры «30/30» после пересчёта размываются.
        mode — вкладка: "modules" (обвесы) или "ammo" (магазин и патроны).
        Возвращает {'weapon':..., 'attachments':[...]} (на вкладке патронов ещё 'tab' и 'ammo') или None."""
        if mode in ("ammo", "ammo_simple"):
            dy = 0          # заголовка «МОДУЛИ» тут нет; меню уже точно вписал Locator
        elif native is not None:
            # меню уже нашёл Locator по двум надписям и вписал заголовок ровно на место —
            # здесь только уточняем сдвиг; курсор мыши на надписи не должен всё отменять
            dy = self.find_header(img, 0.4)
            dy = 0 if dy is None or abs(dy) > 6 else dy
        else:
            dy = self.find_header(img)
        if dy is None:
            return None

        def native_crop(x0, y0, x1, y1, margin=0):
            frame, s, hx, hy = native
            ox, oy = hx - HEADER_POS[0] * s, hy - HEADER_POS[1] * s
            return np.ascontiguousarray(
                frame[max(0, int(oy + (y0 + dy) * s) - margin): int(oy + (y1 + dy) * s) + 1 + margin,
                      max(0, int(ox + x0 * s) - margin): int(ox + x1 * s) + 1 + margin])

        def text(x0, y0, x1, y1, zoom):
            if native is None:
                return self.read_text(img[y0 + dy: y1 + dy, x0:x1], zoom)
            return self.read_text(native_crop(x0, y0, x1, y1), max(1.0, zoom / native[1]))

        # Какая карточка выбрана: у неё оранжевая рамка (всегда) и обычно оранжевая кнопка «МОДУЛИ»
        # (но не всегда — например, когда выбрана ячейка прицела, кнопка просто светло-серая).
        def warm(y0, y1, x0, x1):
            px = img[y0 + dy: y1 + dy, x0:x1].reshape(-1, 3).mean(0)
            return float(px[0]) - float(px[2])   # «теплота»: красный минус синий

        def selected(cx):
            btn = MODULES_BTN if mode == "modules" else (AMMO_BTN[0] - CARD_X[0], AMMO_BTN[1])   # оранжевая кнопка
            button = warm(btn[1] - 4, btn[1] + 4, cx + btn[0] - 4, cx + btn[0] + 4)
            frame = (warm(179, 182, cx + 20, cx + 300) + warm(200, 330, cx, cx + 3)) / 2
            return button + 3 * frame
        scores = {cx: selected(cx) for cx in CARD_X}
        card = max(CARD_X, key=scores.get)

        x0, y0, x1, y1 = CARD_NAME
        weapon_text, wkey = text(card + x0, y0, card + x1, y1, 2)
        weapon = match_weapon(weapon_text, quiet=native is not None)
        if not weapon and native is not None and wkey not in self._miss:
            # мелко (игра в маленьком окне): «M4» читается как «ME» — перечитываем крупнее
            crop = native_crop(card + x0, y0, card + x1, y1, margin=2)
            for zoom in (5, 7, 9):
                big = cv2.copyMakeBorder(cv2.resize(crop, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_CUBIC),
                                         12, 12, 12, 12, cv2.BORDER_REPLICATE)
                res, _ = self.ocr(big)
                again = " ".join(r[1] for r in res) if res else ""
                weapon = match_weapon(again, quiet=True)
                log.debug("Пушка: перечитал крупнее (×%d): %r → %s", zoom, again, weapon)
                if weapon:
                    weapon_text = again
                    break
            if not weapon:
                self._miss.add(wkey)              # на этой же картинке больше не пробуем
                match_weapon(weapon_text)         # теперь — с записью в журнал и unknown_names.txt
        if not weapon:
            return {"weapon": None, "attachments": [], "raw": weapon_text}
        if wkey not in self.mem["ocr"]:
            log.info("Пушка: прочитано %r → %s (карточка %d; выбранность карточек: %s)", weapon_text, weapon,
                     CARD_X.index(card) + 1, [round(scores[c]) for c in CARD_X])
        self.remember("ocr", wkey, weapon_text)
        has_mags = any(weapon in a[3] and a[1] in MAG_TYPES for a in ATTS.values())
        if mode == "ammo" or (mode == "ammo_simple" and has_mags):
            # (у пушки с магазином «без шагов» бывает, когда курсор закрыл рамку «ШАГ 1» — ячейки всё равно смотрим)
            return self.analyze_ammo(img, weapon)
        if mode == "ammo_simple":   # лук, Mosin: магазина нет — патроны те, что видели в ячейке на «Модулях»
            return {"weapon": weapon, "attachments": [], "tab": "ammo", "ammo": AMMO_CHOICE.get(weapon)}

        cands = [n for n, a in ATTS.items() if weapon in a[3] and n in ICONS]
        types = self.mem["slot_types"].setdefault(weapon, {})
        mag_cands = [n for n in cands if ATTS[n][1] in MAG_TYPES]
        mag_icon = None               # (магазин по иконке, точность, отрыв от второго магазина)
        found = []
        rounds = round_icons(weapon)  # коробки патронов: у Mosin, SV98 и т.п. они стоят прямо в ячейке магазина
        slot_ammo = None
        n_slots = int((ROW_RIGHT - SLOT_X0 - SLOT_W) // SLOT_STEP) + 1
        for i in range(n_slots):
            sx = int(SLOT_X0 + i * SLOT_STEP)
            region = img[SLOT_Y + dy - 6: SLOT_Y + dy + SLOT_H + 6, sx - 6: sx + SLOT_W + 6].astype(np.float32)
            if region.std() < 6:      # пустая ячейка
                continue
            group = types.get(str(i))
            key = weapon + "|" + img_hash(region)
            if group == "mag":
                # магазин: главное — цифры на карточке; иконка — подсказка (уверенной иконке верим)
                mag_icon = self.mag_icon(key, region, mag_cands)
                continue
            name = self.mem["slots"].get(key) or self._hold.get(key)
            if name and name.startswith("AMMO|"):   # в ячейке коробка патронов
                slot_ammo = name.split("|")[2]
                continue
            if name:                  # эту ячейку уже видели — ответ мгновенный
                if key in self.mem["slots"]:
                    self._slot_last[(weapon, i)] = name
                if name not in found:
                    found.append(name)
                continue
            if key in self._miss:
                continue

            # Сначала ищем среди обвесов того типа, что раньше стоял в этой ячейке,
            # не нашли — среди всех подходящих к пушке.
            t_slot = time.time()
            best, best_name, how = -1.0, None, ""
            if group:
                same = [n for n in cands if SLOT_GROUP.get(ATTS[n][1]) == group]
                best, best_name = self.best_match(region, same)
                how = f"по типу «{group}» ({len(same)} шт.)"
            if best < self.threshold:
                best, best_name = self.best_match(region, cands)
                how += (" → " if how else "") + f"среди всех ({len(cands)} шт.)"
            log.debug("%s, ячейка %d: искал %s; лучшие: %s; за %.2f с", weapon, i + 1, how,
                      ", ".join(f"{n} {s:.3f}" for n, s in self.last_top), time.time() - t_slot)

            if best < self.threshold and rounds:
                top = self.last_top
                s_r, n_r = self.best_match(region, list(rounds.values()))
                if s_r >= AMMO_SLOT_MIN:
                    slot_ammo = n_r.split("|")[2]
                    self.remember("slots", key, n_r)
                    log.info("%s, ячейка %d: коробка патронов %s (точность %.3f)", weapon, i + 1, slot_ammo, s_r)
                    continue
                self.last_top = top
            second = f"{self.last_top[1][0]} {self.last_top[1][1]:.3f}" if len(self.last_top) > 1 else "—"
            if best_name and ATTS[best_name][1] in MAG_TYPES:
                if i == 0 and best >= 0.4:   # магазин у всех пушек в первой ячейке (с меткой «1»)
                    types["0"] = "mag"
                    self._dirty = True
                    mag_icon = self.mag_icon(key, region, mag_cands)
                log.info("%s, ячейка %d: похоже на магазин (%s %.3f) — его определю по цифрам на карточке",
                         weapon, i + 1, best_name, best)
                self._miss.add(key)
                continue
            if best_name and best >= self.threshold:
                if types.get(str(i)) != SLOT_GROUP.get(ATTS[best_name][1], "other"):
                    types[str(i)] = SLOT_GROUP.get(ATTS[best_name][1], "other")
                    self._dirty = True
                self.remember("slots", key, best_name)
                self._slot_last[(weapon, i)] = best_name
                log.info("%s, ячейка %d: %s (точность %.3f, второе место: %s)", weapon, i + 1, best_name, best, second)
                if best_name not in found:
                    found.append(best_name)
            elif self._holds(weapon, i, best_name, best) and np.percentile(region.mean(2), 99) >= CURSOR_BRIGHT:
                # иконку частично закрывает курсор мыши: это тот же обвес, что был в ячейке только что,
                # и он всё ещё самый похожий — оставляем (иначе плашка мигает, пока водишь мышью).
                # Только если курсор правда есть (яркая стрелка): пустой слот с серым силуэтом рукояти похож
                # на RVG (~0.5–0.66), но в нём нет ни одной яркой точки — его не держим (отзыв в Steam 29.10).
                self._hold[key] = best_name
                log.info("%s, ячейка %d: %s частично закрыт (точность %.3f) — оставляю, он тут уже был",
                         weapon, i + 1, best_name, best)
                if best_name not in found:
                    found.append(best_name)
            else:
                log.warning("%s, ячейка %d: не узнал — лучшее %s %.3f ниже порога %.2f (второе: %s)",
                            weapon, i + 1, best_name, best, self.threshold, second)
                self._miss.add(key)

        # магазин: «30/30» → 30 патронов (нужна вторая цифра — ёмкость)
        mags = {}
        for n, a in ATTS.items():
            if a[1] not in MAG_TYPES or weapon not in a[3]:
                continue
            # ёмкость: из базы (5-й элемент), иначе из названия «… 30 RND …»
            m = re.search(r"\b(\d+) RND\b", n)
            cap = str(a[4]) if len(a) > 4 and a[4] else (m.group(1) if m else None)
            if cap:
                mags.setdefault(cap, n)
        x0, y0, x1, y1 = CARD_CAP
        cap_text, ckey = text(card + x0, y0, card + x1, y1, 5)
        def vote(txt):
            """Голос за ёмкость: «75/75» (обе цифры равны — магазин у продавца полный) — 2 голоса,
            «?/75» — 1 голос. Ёмкости, которой у этой пушки не бывает, — не считаем."""
            mm = re.search(r"(\d+)\s*/\s*(\d+)", txt or "")
            if not mm or mm.group(2) not in mags:
                return None, 0
            return mm.group(2), (2 if mm.group(1) == mm.group(2) else 1)

        # уверенная иконка магазина (≥0.75 и с отрывом от других магазинов этой пушки) — сильный голос
        icon_cap = None
        if mag_icon and mag_icon[0] and mag_icon[1] >= MAG_ICON_SURE and mag_icon[2] >= 0.15:
            ic = next((c for c, n in mags.items() if n == mag_icon[0]), None)
            icon_cap = ic

        first, w = vote(cap_text)
        size = None
        if w == 2 and icon_cap in (None, first):     # «75/75» и иконка не против — надёжно
            size = first
            self.remember("ocr", ckey, cap_text)
        elif native is not None and mags:
            # прочиталось криво (мелко, сжато, размыто): читаем ещё 8 раз по-разному и голосуем.
            # Показываем, только если один вариант уверенно победил — иначе лучше без магазина,
            # чем с неверными цифрами (на сжатой картинке «75» однажды прочиталось как «30»).
            cache_key = "cap|" + ckey + "|" + str(icon_cap)
            size = self.mem["ocr"].get(cache_key)
            if not size and cache_key not in self._miss:
                self._miss.add(cache_key)   # не получилось — на этой же картинке больше не пробуем
                crop = native_crop(card + x0, y0, card + x1, y1, margin=2)
                votes = {first: w} if first else {}
                if icon_cap:
                    votes[icon_cap] = votes.get(icon_cap, 0) + 3
                for zoom in (6, 8, 10, 12):
                    big = cv2.resize(crop, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_CUBIC)
                    for pic in (big, cv2.copyMakeBorder(big, 20, 20, 20, 20, cv2.BORDER_REPLICATE)):
                        res, _ = self.ocr(pic)
                        cap, vw = vote(" ".join(r[1] for r in res) if res else "")
                        if cap:
                            votes[cap] = votes.get(cap, 0) + vw
                ranked = sorted(votes.items(), key=lambda kv: -kv[1])
                top = ranked[0] if ranked else (None, 0)
                second = ranked[1][1] if len(ranked) > 1 else 0
                ok = top[1] >= 3 and top[1] - second >= 2
                log.debug("Магазин (неуверенно): прочитано %r, голоса %s, бывают у пушки: %s → %s",
                          cap_text, votes, sorted(mags), top[0] if ok else "не уверен — не показываю")
                if ok:
                    size = top[0]
                    self.remember("ocr", cache_key, size)
                    self._miss.discard(cache_key)
        if size:
            found.insert(0, mags[size])
        if mags and (weapon, cap_text, size) != getattr(self, "_last_cap", None):
            self._last_cap = (weapon, cap_text, size)
            if size:
                log.info("Магазин: прочитано %r → %s", cap_text, mags[size])
            else:
                log.warning("Магазин не определён: прочитано %r, у пушки бывают: %s", cap_text, sorted(mags))

        if slot_ammo:
            AMMO_CHOICE[weapon] = slot_ammo
        elif not size and not (mag_icon and mag_icon[1] >= 0.5) and weapon in AMMO_CHOICE:
            # магазин сняли — патронов, выбранных раньше на вкладке «Боеприпасы», больше нет
            log.info("%s: магазина нет — забываю патроны %s", weapon, AMMO_CHOICE.pop(weapon))
        self.save()
        out = {"weapon": weapon, "attachments": found}
        if AMMO_CHOICE.get(weapon):   # патроны (из ячейки или с вкладки «Боеприпасы») — для TTK и строки на плашке
            out["ammo"] = AMMO_CHOICE[weapon]
        return out


# ---------------------------------------------------------------- где на экране меню
# Всё распознавание выше работает с «эталонным» кадром 1920×1080. Чтобы подходил любой экран
# (720p…4K, 16:10, 21:9, игра в окне, любой монитор), ищем надпись «МОДУЛИ» в любом месте
# и в любом масштабе, а потом вырезаем этот кусок экрана и приводим его к эталону.
COMMON_SCALES = (0.5, 0.56, 0.62, 0.67, 0.72, 0.78, 0.83, 0.9, 0.95, 1.0, 1.06, 1.12, 1.2, 1.27, 1.33,
                 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0)
COARSE = 0.5          # грубый поиск — на кадре в половину эталона (быстро)
CELLS_OFFSET = (-2, -314)   # где «ЯЧЕЙКИ ДЛЯ ЭКИПИРОВКИ» относительно «МОДУЛИ» (в эталоне)
HEADER_MIN = 0.6            # «МОДУЛИ» похоже хотя бы так
LABEL_MIN = 0.5             # «ЯЧЕЙКИ ДЛЯ ЭКИПИРОВКИ» похоже хотя бы так
PAIR_MIN = 1.25             # и в сумме не меньше (1080p: ~1.9; друг на 1440p: заголовок ~0.72)
# Вкладка «Боеприпасы»: вместо заголовка «МОДУЛИ» на том же месте рамка «// ШАГ 1: ВЫБРАТЬ МАГАЗИН»
# (на 19 снимках вкладки ≥0.995, на остальных ≤0.55). Всё остальное меню — там же, где у «Модулей».
AMMO_OFFSET = (0, -6)       # где рамка относительно места заголовка «МОДУЛИ» (в эталоне)
AMMO_MIN = 0.7
# Вкладка «Боеприпасы» у лука и Mosin (нет шагов «магазин + патроны»): опора — «ЯЧЕЙКИ ДЛЯ ЭКИПИРОВКИ»
# (должна совпасть очень точно) и оранжевая кнопка «БОЕПРИПАСЫ» под карточкой (центр кнопки, эталон).
SIMPLE_MIN = 0.85
AMMO_BTN = (233, 372)
TAB_RU = {"modules": "Модули", "ammo": "Боеприпасы", "ammo_simple": "Боеприпасы"}


def _gray(rgb):
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


class Locator:
    """Находит меню «Модули» на кадре. Место = (масштаб s, x, y надписи на экране)."""

    def __init__(self):
        # Образцы надписей меню для каждого языка игры (2.0: и английская игра). Английские надписи стоят
        # там же, где русские: ATTACHMENTS = «МОДУЛИ», EQUIPMENT SLOTS = «ЯЧЕЙКИ ДЛЯ ЭКИПИРОВКИ»,
        # «// STP 1 - SELECT.MAG» = «// ШАГ 1» — смещения и всё остальное меню общие.
        self.variants = []
        for lang, suffix in (("ru", ""), ("en", "_en")):
            files = [os.path.join(BUNDLE, f"ref_{n}{suffix}.png") for n in ("moduli", "cells", "ammo")]
            if not all(os.path.exists(f) for f in files):
                log.warning("Нет образцов меню для языка игры %s — его не ищу", lang)
                continue
            ref, cells, ammo = (_gray(read_rgb(f)).astype(np.float32) for f in files)
            small = lambda a: cv2.resize(a, None, fx=COARSE, fy=COARSE, interpolation=cv2.INTER_AREA)  # noqa: E731
            self.variants.append({"lang": lang, "ref": ref, "ref_small": small(ref), "cells": cells,
                                  "cells_small": small(cells), "ammo": ammo, "ammo_small": small(ammo)})
        self.game_lang = None       # язык игры, на котором меню нашлось последним
        self._use(0)
        self.mode = "modules"       # какая вкладка нашлась последней: "modules" или "ammo"
        self.near = None
        self.coarse = (-1.0, None)

    def _use(self, i):
        v = self.variants[i]
        self.ref, self.ref_small, self.cells = v["ref"], v["ref_small"], v["cells"]
        self.cells_small, self.ammo, self.ammo_small = v["cells_small"], v["ammo"], v["ammo_small"]
        self._cur = i

    def _order(self):
        """Сначала язык, на котором меню нашлось в прошлый раз, потом остальные."""
        return [self._cur] + [i for i in range(len(self.variants)) if i != self._cur]

    def _found(self, i):
        lang = self.variants[i]["lang"]
        if lang != self.game_lang:
            log.info("Язык меню игры: %s", {"ru": "русский", "en": "английский"}.get(lang, lang))
            self.game_lang = lang

    def check_ammo(self, frame_gray, s, hx, hy, wide=False):
        """Вкладка «Боеприпасы» в этом месте? (hx, hy — где был бы заголовок «МОДУЛИ»).
        → (точность, s, hx, hy) в тех же координатах, что и у «Модулей», или None."""
        th, tw = self.ammo.shape
        ax, ay = hx + AMMO_OFFSET[0] * s, hy + AMMO_OFFSET[1] * s
        pad = int((14 if wide else 8) * s) + 2
        x0, y0 = max(0, int(ax) - pad), max(0, int(ay) - pad)
        x1 = min(frame_gray.shape[1], int(ax + tw * s * 1.07) + pad)
        y1 = min(frame_gray.shape[0], int(ay + th * s * 1.07) + pad)
        crop = frame_gray[y0:y1, x0:x1]
        if crop.size == 0:
            return None
        best = None
        for k in ((0.94, 0.96, 0.98, 1.0, 1.02, 1.04, 1.06) if wide else (0.98, 1.0, 1.02)):
            ss = s * k
            c = cv2.resize(crop, None, fx=1 / ss, fy=1 / ss,
                           interpolation=cv2.INTER_AREA if ss > 1 else cv2.INTER_CUBIC).astype(np.float32)
            if c.shape[0] < th or c.shape[1] < tw:
                continue
            _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(c, self.ammo, cv2.TM_CCOEFF_NORMED))
            if best is None or score > best[0]:
                best = (score, ss, x0 + loc[0] * ss - AMMO_OFFSET[0] * ss, y0 + loc[1] * ss - AMMO_OFFSET[1] * ss)
        if not best or best[0] < AMMO_MIN:
            return None
        label = self.confirm(frame_gray, *best[1:])
        if label < LABEL_MIN or best[0] + label < PAIR_MIN:
            return None
        return ((best[0] + label) / 2,) + best[1:]

    def check_simple(self, frame_gray, s, hx, hy, frame_rgb):
        """Вкладка «Боеприпасы» без шагов (лук, Mosin — магазина нет): только «ЯЧЕЙКИ ДЛЯ ЭКИПИРОВКИ»
        и оранжевая кнопка «БОЕПРИПАСЫ» под одной из карточек. → (точность, s, hx, hy) или None."""
        label = self.confirm(frame_gray, s, hx, hy)
        if label < SIMPLE_MIN or frame_rgb is None:
            return None
        for card in CARD_X:
            bx = hx + (AMMO_BTN[0] + card - CARD_X[0] - HEADER_POS[0]) * s
            by = hy + (AMMO_BTN[1] - HEADER_POS[1]) * s
            px = frame_rgb[max(0, int(by - 4 * s)): int(by + 4 * s) + 1, max(0, int(bx - 6 * s)): int(bx + 6 * s) + 1]
            if px.size and float(px[..., 0].mean()) - float(px[..., 2].mean()) >= 80:
                return (label, s, hx, hy)
        return None

    def check_any(self, frame_gray, s, hx, hy, frame_rgb=None):
        """Меню там же, где было? Сначала на языке, на котором нашлось в прошлый раз, потом на другом."""
        for i in self._order():
            self._use(i)
            hit = self._check_any_one(frame_gray, s, hx, hy, frame_rgb)
            if hit:
                self._found(i)
                return hit
        return None

    def _check_any_one(self, frame_gray, s, hx, hy, frame_rgb=None):
        """Меню там же, где было? «Модули», «Боеприпасы», «Боеприпасы» без шагов (запоминает, какая вкладка)."""
        hit = self.check(frame_gray, s, hx, hy)
        if hit:
            self.mode = "modules"
            return hit
        hit = self.check_ammo(frame_gray, s, hx, hy)
        if hit:
            self.mode = "ammo"
            return hit
        hit = self.check_simple(frame_gray, s, hx, hy, frame_rgb)
        if hit:
            self.mode = "ammo_simple"
        return hit

    def confirm(self, frame_gray, s, hx, hy):
        """Есть ли «ЯЧЕЙКИ ДЛЯ ЭКИПИРОВКИ» там, где она должна быть относительно заголовка."""
        th, tw = self.cells.shape
        pad = int(10 * s) + 2
        cx, cy = hx + CELLS_OFFSET[0] * s, hy + CELLS_OFFSET[1] * s
        x0, y0 = int(cx) - pad, int(cy) - pad
        x1, y1 = int(cx + tw * s) + pad, int(cy + th * s) + pad
        x0, y0 = max(0, x0), max(0, y0)
        crop = frame_gray[y0:y1, x0:x1]
        if crop.size == 0:
            return -1.0
        best = -1.0
        for k in (0.97, 1.0, 1.03):   # шрифт этой надписи тоже может чуть отличаться по размеру
            ss = s * k
            c = cv2.resize(crop, None, fx=1 / ss, fy=1 / ss,
                           interpolation=cv2.INTER_AREA if ss > 1 else cv2.INTER_CUBIC).astype(np.float32)
            if c.shape[0] < th or c.shape[1] < tw:
                continue
            best = max(best, float(cv2.matchTemplate(c, self.cells, cv2.TM_CCOEFF_NORMED).max()))
        return best

    def check(self, frame_gray, s, hx, hy, wide=False):
        """Есть ли надпись точно в этом месте. wide — подбирать масштаб шире (±6%):
        нужно при поиске, если в игре нестандартный масштаб интерфейса."""
        th, tw = self.ref.shape
        pad = int((14 if wide else 8) * s) + 2
        x0, y0 = max(0, int(hx) - pad), max(0, int(hy) - pad)
        x1 = min(frame_gray.shape[1], int(hx + tw * s * 1.07) + pad)
        y1 = min(frame_gray.shape[0], int(hy + th * s * 1.07) + pad)
        crop = frame_gray[y0:y1, x0:x1]
        if crop.size == 0:
            return None
        best = None
        for k in ((0.94, 0.96, 0.98, 1.0, 1.02, 1.04, 1.06) if wide else (0.98, 1.0, 1.02)):
            ss = s * k
            c = cv2.resize(crop, None, fx=1 / ss, fy=1 / ss,
                           interpolation=cv2.INTER_AREA if ss > 1 else cv2.INTER_CUBIC).astype(np.float32)
            if c.shape[0] < th or c.shape[1] < tw:
                continue
            r = cv2.matchTemplate(c, self.ref, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(r)
            if best is None or score > best[0]:
                best = (score, ss, x0 + loc[0] * ss, y0 + loc[1] * ss)
        label = self.confirm(frame_gray, *best[1:]) if best and best[0] >= HEADER_MIN else None
        if best and (self.near is None or best[0] + (label or 0) > self.near["заголовок"] + (self.near["подтверждение"] or 0)):
            self.near = {"заголовок": round(best[0], 3), "подтверждение": None if label is None else round(label, 3),
                         "масштаб": round(best[1], 3), "место": (int(best[2]), int(best[3]))}
        # Нужны ОБЕ надписи на своих местах. Пороги мягкие: на других разрешениях игра рисует шрифт
        # чуть иначе, чем образец с 1080p (на 1440p заголовок похож на ~0.72). От кнопок «МОДУЛИ»
        # защищает вторая надпись — у кнопок она ~0.35.
        if label is not None and label >= LABEL_MIN and best[0] + label >= PAIR_MIN:
            return ((best[0] + label) / 2,) + best[1:]   # (точность, s, x, y)
        return None

    def search(self, frame_gray, scales, frame_rgb=None):
        """Ищем меню по всему кадру — на каждом языке игры (сначала на том, где нашлось в прошлый раз)."""
        near, coarse = None, (-1.0, None)
        for i in self._order():
            self._use(i)
            found = self._search_one(frame_gray, scales, frame_rgb)
            if found:
                self._found(i)
                return found
            if self.near and (near is None or self.near["заголовок"] > near["заголовок"]):
                near = dict(self.near, язык=self.variants[i]["lang"])
            coarse = max(coarse, self.coarse, key=lambda c: c[0])
        self.near, self.coarse = near, coarse   # для журнала: насколько близко подошли (на лучшем языке)
        return None

    def _search_one(self, frame_gray, scales, frame_rgb=None):
        """Ищем надпись по всему кадру в нескольких масштабах.
        Слово «МОДУЛИ» есть ещё и на кнопках под карточками — поэтому берём самое точное совпадение."""
        best = None
        self.near = None            # для журнала: насколько близко подошли, если не нашли
        self.coarse = (-1.0, None)  # лучшее грубое совпадение (точность, масштаб)
        # большой кадр (1440p, 4K) один раз уменьшаем до ~720 строк — дальше масштабы перебираем
        # уже по нему: в разы быстрее, чем каждый раз уменьшать полный кадр
        b = min(1.0, 720 / frame_gray.shape[0])
        base = frame_gray if b >= 1 else cv2.resize(frame_gray, None, fx=b, fy=b, interpolation=cv2.INTER_AREA)
        for s in scales:
            f = COARSE / s
            src, rel = (base, f / b) if f / b <= 1 else (frame_gray, f)
            small = cv2.resize(src, None, fx=rel, fy=rel, interpolation=cv2.INTER_AREA).astype(np.float32)
            if small.shape[0] < self.ref_small.shape[0] or small.shape[1] < self.ref_small.shape[1]:
                continue
            r = cv2.matchTemplate(small, self.ref_small, cv2.TM_CCOEFF_NORMED)
            r = np.nan_to_num(r, nan=-1)
            top = float(r.max())
            if top > self.coarse[0]:
                self.coarse = (round(top, 3), s)
            for _ in range(4):   # несколько лучших мест на этом масштабе
                _, score, _, loc = cv2.minMaxLoc(r)
                if score < 0.55:
                    break
                hit = self.check(frame_gray, s, loc[0] / f, loc[1] / f, wide=True)
                if hit and (best is None or hit[0] > best[0]):
                    best = hit
                r[max(0, loc[1] - 3): loc[1] + 4, max(0, loc[0] - 6): loc[0] + 7] = -1
            if best and best[0] >= 0.8:   # обе надписи уверенно на месте — дальше не ищем
                break
        if best:
            self.mode = "modules"
            return best[1:]
        # «Модулей» нет — может, открыта вкладка «Боеприпасы»
        for s in scales:
            f = COARSE / s
            src, rel = (base, f / b) if f / b <= 1 else (frame_gray, f)
            small = cv2.resize(src, None, fx=rel, fy=rel, interpolation=cv2.INTER_AREA).astype(np.float32)
            if small.shape[0] < self.ammo_small.shape[0] or small.shape[1] < self.ammo_small.shape[1]:
                continue
            r = np.nan_to_num(cv2.matchTemplate(small, self.ammo_small, cv2.TM_CCOEFF_NORMED), nan=-1)
            for _ in range(3):
                _, score, _, loc = cv2.minMaxLoc(r)
                if score < 0.55:
                    break
                hit = self.check_ammo(frame_gray, s, loc[0] / f - AMMO_OFFSET[0] * s,
                                      loc[1] / f - AMMO_OFFSET[1] * s, wide=True)
                if hit and (best is None or hit[0] > best[0]):
                    best = hit
                r[max(0, loc[1] - 3): loc[1] + 4, max(0, loc[0] - 6): loc[0] + 7] = -1
            if best and best[0] >= 0.8:
                break
        if best:
            self.mode = "ammo"
            return best[1:]
        # и не она — может, «Боеприпасы» без шагов (лук, Mosin): ищем «ЯЧЕЙКИ ДЛЯ ЭКИПИРОВКИ»
        if frame_rgb is not None:
            for s in scales:
                f = COARSE / s
                src, rel = (base, f / b) if f / b <= 1 else (frame_gray, f)
                small = cv2.resize(src, None, fx=rel, fy=rel, interpolation=cv2.INTER_AREA).astype(np.float32)
                if small.shape[0] < self.cells_small.shape[0] or small.shape[1] < self.cells_small.shape[1]:
                    continue
                r = np.nan_to_num(cv2.matchTemplate(small, self.cells_small, cv2.TM_CCOEFF_NORMED), nan=-1)
                for _ in range(2):
                    _, score, _, loc = cv2.minMaxLoc(r)
                    if score < 0.6:
                        break
                    hit = self.check_simple(frame_gray, s, loc[0] / f - CELLS_OFFSET[0] * s,
                                            loc[1] / f - CELLS_OFFSET[1] * s, frame_rgb)
                    if hit and (best is None or hit[0] > best[0]):
                        best = hit
                    r[max(0, loc[1] - 3): loc[1] + 4, max(0, loc[0] - 6): loc[0] + 7] = -1
                if best:
                    self.mode = "ammo_simple"
                    return best[1:]
        return None


def to_reference(frame, s, hx, hy):
    """Вырезает с экрана кусок вокруг меню и приводит его к эталону 1920×1080."""
    ox, oy = hx - HEADER_POS[0] * s, hy - HEADER_POS[1] * s
    # нужна только левая часть эталона с меню (до x≈1200, y≈700) — остальное заполняем чёрным
    vw, vh = 1200, 700
    x0, y0 = int(round(ox)), int(round(oy))
    x1, y1 = int(round(ox + vw * s)), int(round(oy + vh * s))
    H, W = frame.shape[:2]
    crop = frame[max(0, y0):min(H, y1), max(0, x0):min(W, x1)]
    crop = cv2.copyMakeBorder(crop, max(0, -y0), max(0, y1 - H), max(0, -x0), max(0, x1 - W),
                              cv2.BORDER_CONSTANT, value=(0, 0, 0))
    part = cv2.resize(crop, (vw, vh), interpolation=cv2.INTER_AREA if s > 1 else cv2.INTER_CUBIC)
    out = np.zeros((1080, 1920, 3), np.uint8)
    out[:vh, :vw] = part
    return out


def placement_for(mon, s, hx, hy, w=380):
    """Где ставить окно: там же относительно меню, где оно стоит на обычном экране 1920×1080."""
    x = mon["left"] + hx + (1290 - HEADER_POS[0]) * s
    y = mon["top"] + hy + (245 - HEADER_POS[1]) * s
    x = min(max(x, mon["left"]), mon["left"] + mon["width"] - w)
    y = min(max(y, mon["top"]), mon["top"] + mon["height"] - 200)
    return int(x), int(y)


def default_scales(h, w):
    """Сначала самые вероятные масштабы для этого экрана, потом остальные."""
    first = [h / 1080, w / 1920]
    return list(dict.fromkeys([round(x, 3) for x in first])) + [x for x in COMMON_SCALES if all(abs(x - f) > 0.02 for f in first)]


# ---------------------------------------------------------------- расчёт статов
STATS = [
    ("ADS zoom", "Зум прицела", None),
    ("Vertical recoil", "Отдача вверх", "low"),
    ("Horizontal recoil", "Отдача вбок", "low"),
    ("Spread", "Разброс", "low"),
    ("ADS time", "Время прицеливания", "low"),
    ("Aim sway", "Качание прицела", "low"),
    ("Equip speed", "Скорость доставания", "high"),
    ("Stabilisation time", "Время стабилизации", "low"),
    ("Sway speed", "Скорость качания", "low"),
    ("Sway from horizontal movement", "Качание при стрейфе", "low"),
    ("Sway from vertical movement", "Качание в прыжке", "low"),
    ("Translation stabilisation", "Стабилиз. в движении", "low"),
    ("Rotation stabilisation", "Стабилиз. при повороте", "low"),
]
SLOT_RU = {"Optic": "ПРИЦЕЛ", "Muzzle": "ДУЛО", "Suppressor": "ГЛУШИТЕЛЬ", "Foregrip": "РУКОЯТЬ",
           "Magazine": "МАГАЗИН", "Ammunition": "МАГАЗИН", "Bipod": "СОШКИ"}
CLASS_RU = {"Assault Rifle": "ШТУРМОВАЯ", "Submachine Gun": "ПП", "Sniper Rifle": "СНАЙПЕРКА",
            "Marksman Rifle": "МАРКСМАН", "Light Machine Gun": "ПУЛЕМЁТ", "Pistol": "ПИСТОЛЕТ",
            "Shotgun": "ДРОБОВИК", "Launcher": "ГРАНАТОМЁТ", "Bow": "ЛУК"}
MAX_ROWS = 10


def _sec(x):
    return f"{x:.2f}".replace(".", T(",", "."))     # 0,49 по-русски, 0.49 по-английски


def stat_totals(att_names, weapon=None):
    """Во сколько раз обвесы меняют каждый стат: ({стат: множитель}, родное время прицеливания или None).
    Время прицеливания у пушки в базе есть в секундах (AK74 — 0.45 с), обвесы его умножают."""
    base_ads = None
    w = WEAPONS.get(weapon) if weapon else None
    if w and w[5]:
        m = re.match(r"([\d.]+)", w[5])
        base_ads = float(m.group(1)) if m else None
    # Эффект обвеса: [стат, значение] — умножить; [стат, значение, "="] — заменить; [.., "+"] — прибавить.
    # Итог как в игре: (родное или заменённое + прибавки) × множители.
    mult, over, add = {}, {}, {}
    for n in att_names:
        if n not in ATTS:
            continue
        for e in ATTS[n][2]:
            attr, v, op = e[0], float(e[1]), (e[2] if len(e) > 2 else "x")
            if op == "=":
                over[attr] = v
            elif op == "+":
                add[attr] = add.get(attr, 0.0) + v
            else:
                mult[attr] = mult.get(attr, 1.0) * v
    total = {}
    for attr in set(mult) | set(over) | set(add):
        base = base_ads if (attr == "ADS time" and base_ads) else 1.0
        value = (over.get(attr, base) + add.get(attr, 0.0)) * mult.get(attr, 1.0)
        total[attr] = value / base          # во сколько раз изменилось относительно пушки без обвесов
    return total, base_ads


KEY_STATS = ("Vertical recoil", "Horizontal recoil", "Spread", "ADS time")   # 4 главных (без «Подробно»)


def compute_rows(att_names, weapon=None, only=None):
    """Строки плашки: (подпись, что лучше, множитель, своё значение справа или None).
    Время прицеливания — в секундах; для остальных статов родных чисел в базе нет — проценты.
    only — показать именно эти статы (даже если обвес их не меняет); None — все, которые меняются."""
    total, base_ads = stat_totals(att_names, weapon)
    rows = []
    for key, ru, better in STATS:
        changed = key in total and abs(total[key] - 1) >= 0.005
        if only is not None and key not in only:
            continue
        if only is None and not changed:
            continue
        m = total.get(key, 1.0)
        value = None
        if key == "ADS time" and base_ads:
            new = round(base_ads * m, 2)
            diff = new - round(base_ads, 2)
            if changed:
                ru = f"ADS {_sec(base_ads)}→{_sec(new)}" + T(" с", " s")
                value = f"{'+' if diff > 0 else '−' if diff < 0 else '±'}{_sec(abs(diff))}" + T(" с", " s")
            else:
                ru, value = T("Время ADS", "ADS time"), f"{_sec(base_ads)}" + T(" с", " s")
        rows.append((ru, better, m, value))
    return rows[:MAX_ROWS]


def compute_pair_rows(now_names, advised_names, weapon=None, only=None):
    """Статы для плашки с оружейником: что стоит сейчас и сколько станет с советом — в одной строке.
    [(подпись, что лучше, множитель сейчас, своё значение сейчас, множитель с советом, значение с советом)].
    Набор строк — по сборке «сейчас», плюс статы, которые меняет только совет (в «Подробно»)."""
    total_now, base_ads = stat_totals(now_names, weapon)
    total_adv, _ = stat_totals(advised_names, weapon)
    out = []
    for key, ru, better in STATS:
        if better is None:          # зум задаёт прицел, оружейник его не советует
            continue
        a, b = total_now.get(key, 1.0), total_adv.get(key, 1.0)
        if only is not None and key not in only:
            continue
        if only is None and abs(a - 1) < 0.005 and abs(b - 1) < 0.005:
            continue
        label, val_a, val_b = ru, None, None
        if key == "ADS time" and base_ads:
            label = T("Время ADS", "ADS time")
            val_a, val_b = f"{_sec(base_ads * a)}" + T(" с", " s"), f"{_sec(base_ads * b)}" + T(" с", " s")
        out.append((label, better, a, val_a, b, val_b))
    return out[:MAX_ROWS]


# ---------------------------------------------------------------- оружейник
# Какие слоты он советует (прицел и магазин — дело вкуса: кратность и патроны) и какие типы обвесов в них идут.
GUNSMITH_SLOTS = (("ДУЛО", ("Muzzle", "Suppressor")), ("РУКОЯТЬ", ("Foregrip", "Bipod")))
# Что считать «лучше»: вес каждого стата (меньше множитель — лучше). Сравнение по сумме логарифмов —
# так −20% отдачи и +20% времени ADS при равных весах примерно гасят друг друга.
GUNSMITH_MODES = (("Баланс", {"Vertical recoil": 0.35, "Horizontal recoil": 0.25, "Spread": 0.2, "ADS time": 0.2}),
                  ("Отдача", {"Vertical recoil": 0.6, "Horizontal recoil": 0.4}),
                  ("ADS", {"ADS time": 0.8, "Spread": 0.2}))
_GUNSMITH_CACHE = {}


def gunsmith_advice(weapon, installed, mode=0):
    """Совет оружейника: ([(слот, лучший обвес, что стоит в этом слоте сейчас или None)], сборка с советом).
    Сборка с советом = то, что стоит, но в советуемых слотах — рекомендованное."""
    mode = mode if 0 <= mode < len(GUNSMITH_MODES) else 0
    key = (weapon, mode, tuple(sorted(installed)))
    if key in _GUNSMITH_CACHE:
        return _GUNSMITH_CACHE[key]
    t = time.time()
    weights = GUNSMITH_MODES[mode][1]
    advice, build = [], [n for n in installed]
    for label, types in GUNSMITH_SLOTS:
        opts = [n for n, a in ATTS.items() if a[1] in types and weapon in a[3]]
        if not opts:
            continue

        def score(n):
            tot, _ = stat_totals([n], weapon)
            return sum(w * math.log(max(tot.get(k, 1.0), 1e-6)) for k, w in weights.items())

        best = min(opts, key=lambda n: (round(score(n), 6), n))
        cur = next((n for n in installed if n in ATTS and ATTS[n][1] in types), None)
        advice.append((label, best, cur))
        build = [n for n in build if not (n in ATTS and ATTS[n][1] in types)] + [best]
    _GUNSMITH_CACHE[key] = (advice, build)
    log.debug("Оружейник %s (%s): совет %s, сборка %s — за %.0f мс", weapon, GUNSMITH_MODES[mode][0],
              advice, build, (time.time() - t) * 1000)
    return advice, build


def stat_text(key, m, base_ads):
    """Как показать итог стата: «−31%», «0,38 с», «1.5×», «±0%». Зума нет в базе — «—»."""
    if key == "ADS zoom":
        return "—" if m is None else f"{m:.1f}×".replace(".0×", "×")
    if key == "ADS time" and base_ads:
        return f"{_sec(base_ads * m)}" + T(" с", " s")
    pct = round((m - 1) * 100)
    return f"{'+' if pct > 0 else '−' if pct < 0 else '±'}{abs(pct)}%"


def compute_compare(old, new, weapon=None, only=None):
    """Сравнение двух сборок одной пушки: [(подпись, было, станет, итог)], итог: 1 — лучше, −1 — хуже, 0 — так же.
    Показываем статы, которые хоть в одной из сборок отличаются от голой пушки.
    only — без «Подробно»: только эти статы и всегда (даже если не меняются), как в обычной плашке."""
    t_old, base_ads = stat_totals(old, weapon)
    t_new, _ = stat_totals(new, weapon)
    rows = []
    for key, ru, better in STATS:
        a, b = t_old.get(key, 1.0), t_new.get(key, 1.0)
        if only is not None:
            if key not in only:
                continue
        elif abs(a - 1) < 0.005 and abs(b - 1) < 0.005:
            continue
        if key == "ADS zoom":   # зум задаёт прицел; у некоторых (Spectr 4x) в базе его нет — не «1×», а «—»
            a, b = t_old.get(key), t_new.get(key)
        was, now = stat_text(key, a, base_ads), stat_text(key, b, base_ads)
        if better is None or was == now:
            verdict = 0
        else:
            verdict = 1 if (b < a) == (better == "low") else -1
        rows.append((T("Время ADS", "ADS time") if key == "ADS time" and base_ads else ru, was, now, verdict))
    return rows[:MAX_ROWS + 2]


def compare_view(state):
    """Сравнение с запомненной сборкой (Ctrl+Shift+S). Возвращает (было, сейчас, ушло, пришло)
    или None — сборка не запомнена или не изменилась."""
    if not state or not state.get("weapon"):
        return None
    cur = list(state.get("attachments") or [])
    saved = state.get("saved")
    if saved is None or sorted(saved) == sorted(cur):
        return None
    return list(saved), cur, [n for n in saved if n not in cur], [n for n in cur if n not in saved]


# ---------------------------------------------------------------- TTK
TTK_DIST = 50                      # дистанция для TTK на плашке, м (как по умолчанию у MetaForge)
TTK_COLS = ((0, "БЕЗ БРОНИ"), (1, "БРОНЯ 1"), (2, "БРОНЯ 2"), (3, "БРОНЯ 3"), (4, "БРОНЯ 4"))
TTK_ROWS = (("torso_upper", "Тело"), ("head", "Голова"))
AMMO_RU = {"FMJ": "обычные (FMJ)", "HollowPoint": "экспансивные (HP)", "ArmorPiercing": "бронебойные (AP)"}
AMMO_SHORT = {"FMJ": "Обычные", "HollowPoint": "HP", "ArmorPiercing": "AP"}
AMMO_NOTE = {"FMJ": "ровный урон по всем целям",
             "HollowPoint": "больше урона по телу, намного хуже по броне",
             "ArmorPiercing": "лучше по броне, чуть меньше урона без неё"}
_TTK_CACHE = {}


def ttk_cells(weapon, rnd, hit, dist=TTK_DIST):
    """[(выстрелов, секунд) по колонкам TTK_COLS] или None — нет баллистики для этой пушки."""
    key = (weapon, rnd, hit, dist)
    if key not in _TTK_CACHE:
        import ballistics
        out = None
        if BALL and ballistics.weapon(BALL, weapon):
            out = []
            for level, _ in TTK_COLS:
                r = ballistics.ttk(BALL, weapon, rnd, level, hit, dist)
                out.append((r["shots"], round(r["ms"] / 1000, 2) if r["ms"] is not None else None) if r else (None, None))
        _TTK_CACHE[key] = out
    return _TTK_CACHE[key]


TTK_LIMIT = {"Sniper Rifle": 1000, "Marksman Rifle": 800}   # до скольки метров проверять падение урона (иначе 500)
TTK_HOLD = 300                     # урон не падает дольше этого — «Урон держит до … м», кнопок дистанции нет
_TTK_PLAN = {}


def ttk_distances(weapon):
    """Кнопки дистанции TTK для пушки: (кнопки, держит_до).
    Число пуль в тело (броня 0–4, обычные патроны) не меняется дольше TTK_HOLD м → (None, до скольки держит).
    Иначе кнопки: первая — 50 м, следующие — круглые дистанции, где пуль нужно больше, чем на предыдущей
    (кнопок с теми же числами не бывает). Пример: PP-19 Bizon → [50, 250, …]; M4 → (None, 500)."""
    if weapon in _TTK_PLAN:
        return _TTK_PLAN[weapon]
    import ballistics
    t = time.time()
    w = BALL and ballistics.weapon(BALL, weapon)
    if not w:
        return None, None
    limit = TTK_LIMIT.get(w.get("weaponClass"), 500)

    def shots(d):
        return [(ballistics.ttk(BALL, w, "FMJ", lv, "torso_upper", d) or {}).get("shots") for lv, _ in TTK_COLS]

    base, first = shots(5), None
    for d in range(10, limit + 1, 5):
        if shots(d) != base:
            first = d - 5
            break
    if first is None:
        plan = (None, limit)
    elif first >= TTK_HOLD:
        plan = (None, first // 50 * 50)
    else:   # 50 м, дальше — только круглые дистанции, где пуль уже больше, чем на предыдущей кнопке
        buttons, cur = [TTK_DIST], shots(TTK_DIST)
        for d in (75, 100, 125, 150, 175, 200, 250, 300, 350, 400, 500, 600, 800, 1000):
            if d <= limit and len(buttons) < 3 and shots(d) != cur:
                buttons.append(d)
                cur = shots(d)
        plan = (buttons, None)
    _TTK_PLAN[weapon] = plan
    log.info("TTK %s: дистанции %s — за %.0f мс", weapon, plan, (time.time() - t) * 1000)
    return plan


def ttk_payload(weapon, tab=None, ammo=None, dist=TTK_DIST):
    """Что показать в блоке TTK: {"title", "cols", "rows": [[подпись, [[выстрелов, секунд], …], выбрано?], …]}.
    «Модули» — тело и голова с выбранными патронами; «Боеприпасы» — в тело, все виды патронов (выбранные подсвечены)."""
    import ballistics
    t = time.time()
    w = BALL and ballistics.weapon(BALL, weapon)
    if not w:
        log.debug("TTK %s: нет баллистики", weapon)
        return None
    cols = [name for _, name in TTK_COLS]
    # дробовики: в базе MetaForge только картечь, у нарезной пули нет баллистики — так и пишем, цифры не выдумываем
    shotgun = w.get("weaponClass") == "Shotgun"
    note = T("Нарезная пуля — нет данных", "Rifled slug — no data") if shotgun else None
    if tab == "ammo":
        loads = ballistics.loads(BALL, w)
        bow = "Bow" in (w.get("weaponClass") or "")
        rows = [[T("Стрелы", "Arrows") if bow else T("Картечь", "Buckshot") if shotgun and r == "FMJ"
                 else AMMO_SHORT.get(r, r), ttk_cells(weapon, r, "torso_upper", dist), r == ammo] for r in loads]
        title = T("TTK в тело  ·  по патронам", "TTK to body  ·  by ammo")
    else:
        rnd = ammo or "FMJ"
        rows = [[label, ttk_cells(weapon, rnd, hit, dist), False] for hit, label in TTK_ROWS]
        title = ("TTK  ·  " + T("картечь", "buckshot")) if shotgun and rnd == "FMJ" else \
            f"TTK  ·  {AMMO_RU.get(rnd, rnd).split(' (')[0]}" + T(" патроны", " ammo")
    out = {"title": title, "cols": cols, "rows": [[a, [list(c) for c in cells], sel] for a, cells, sel in rows if cells],
           "best": tab == "ammo", "dist": dist}   # best — подсветить зелёным самые быстрые патроны в колонке
    if note and tab == "ammo":
        out["note"] = note
    log.debug("TTK %s (%s): %s — за %.0f мс", weapon, tab or "modules", out["rows"], (time.time() - t) * 1000)
    return out if out["rows"] else None


# ---------------------------------------------------------------- английский интерфейс (2.0)
STATS_EN = {"ADS zoom": "Sight zoom", "Vertical recoil": "Vertical recoil", "Horizontal recoil": "Horizontal recoil",
            "Spread": "Spread", "ADS time": "ADS time", "Aim sway": "Aim sway", "Equip speed": "Equip speed",
            "Stabilisation time": "Stabilisation time", "Sway speed": "Sway speed",
            "Sway from horizontal movement": "Sway when strafing", "Sway from vertical movement": "Sway when jumping",
            "Translation stabilisation": "Moving stabilisation", "Rotation stabilisation": "Turning stabilisation"}
SLOT_EN = {"Optic": "SIGHT", "Muzzle": "MUZZLE", "Suppressor": "SUPPRESSOR", "Foregrip": "GRIP",
           "Magazine": "MAGAZINE", "Ammunition": "MAGAZINE", "Bipod": "BIPOD"}
CLASS_EN = {"Assault Rifle": "ASSAULT RIFLE", "Submachine Gun": "SMG", "Sniper Rifle": "SNIPER",
            "Marksman Rifle": "MARKSMAN", "Light Machine Gun": "LMG", "Pistol": "PISTOL",
            "Shotgun": "SHOTGUN", "Launcher": "LAUNCHER", "Bow": "BOW"}


def apply_language():
    """Выбран английский — подменить русские надписи в общих таблицах (статы, слоты, классы, патроны, TTK, оружейник)."""
    global STATS, TTK_COLS, TTK_ROWS, GUNSMITH_SLOTS, GUNSMITH_MODES
    if LANG["code"] != "en":
        return
    STATS = [(k, STATS_EN.get(k, ru), better) for k, ru, better in STATS]
    SLOT_RU.update(SLOT_EN)
    CLASS_RU.update(CLASS_EN)
    TAB_RU.update({"modules": "ATTC.", "ammo": "Ammo", "ammo_simple": "Ammo"})   # как в английской игре
    AMMO_RU.update({"FMJ": "standard (FMJ)", "HollowPoint": "hollow point (HP)", "ArmorPiercing": "armor piercing (AP)"})
    AMMO_SHORT.update({"FMJ": "Standard"})
    AMMO_NOTE.update({"FMJ": "same damage against everything", "HollowPoint": "more body damage, much worse against armor",
                      "ArmorPiercing": "better against armor, a bit less damage without it"})
    TTK_COLS = ((0, "NO ARMOR"), (1, "ARMOR 1"), (2, "ARMOR 2"), (3, "ARMOR 3"), (4, "ARMOR 4"))
    TTK_ROWS = (("torso_upper", "Body"), ("head", "Head"))
    GUNSMITH_SLOTS = (("MUZZLE", GUNSMITH_SLOTS[0][1]), ("GRIP", GUNSMITH_SLOTS[1][1]))
    GUNSMITH_MODES = tuple((en, w) for en, (_, w) in zip(("Balance", "Recoil", "ADS"), GUNSMITH_MODES))
    _TTK_CACHE.clear()
    _GUNSMITH_CACHE.clear()
    log.info("Язык программы: English")


# ---------------------------------------------------------------- окно поверх игры
BG, LINE, TEXT, DIM, ACCENT, GOOD, BAD = "#0e100c", "#3a3f30", "#e9ecdf", "#9aa38a", "#e8a33a", "#5fd068", "#ff5a4e"
FONT = "Bahnschrift"


class OverlayWindow:
    W = 380

    def __init__(self, settings):
        import tkinter as tk
        self.tk = tk
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", settings["opacity"])
        self.root.configure(bg=BG)
        self.auto = not (isinstance(settings["x"], int) and isinstance(settings["y"], int))
        if self.auto:   # пока игра не найдена — место как на обычном экране основного монитора
            sw, sh = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
            s = sh / 1080
            self.x, self.y = placement_for({"left": 0, "top": 0, "width": sw, "height": sh},
                                           s, HEADER_POS[0] * s, HEADER_POS[1] * s)
        else:
            self.x, self.y = settings["x"], settings["y"]
        self.canvas = tk.Canvas(self.root, width=self.W, height=100, bg=BG, highlightthickness=0)
        self.canvas.pack()
        self.root.geometry(f"+{self.x}+{self.y}")
        self.root.update_idletasks()
        self._click_through()
        self.root.withdraw()
        self.shown = False
        self.last = None
        self.flash_until = 0.0
        self.settings = settings
        self.state = None
        # Кликабельные места плашки (галочки и кнопки): [(x0, y0, x1, y1, что сделать)].
        # Вся остальная плашка пропускает клики в игру; окно «ловит» мышь, только пока курсор над такой зоной.
        self.zones = []
        self.catching = False
        self.canvas.bind("<Button-1>", self._on_click)
        self.root.after(80, self._watch_mouse)

    def flash(self, title, subtitle, seconds=8):
        """Плашка «программа запущена» — висит несколько секунд, чтобы точно заметить."""
        c, W, pad = self.canvas, self.W, 16
        c.delete("all")
        self.zones = []          # на плашке-сообщении кликать нечего
        c.create_rectangle(0, 0, 6, 2000, fill=ACCENT, width=0)
        t1 = c.create_text(pad, 14, text="✔ " + title, fill=GOOD, font=(FONT, 15, "bold"), anchor="nw",
                           width=W - 2 * pad)
        y = c.bbox(t1)[3] + 8
        t2 = c.create_text(pad, y, text=subtitle, fill=TEXT, font=(FONT, 11), anchor="nw", width=W - 2 * pad)
        h = c.bbox(t2)[3] + 16   # высота по тексту — длинная подсказка переносится на новую строку
        c.config(height=h)
        self.root.geometry(f"{W}x{h}+{self.x}+{self.y}")
        self.root.deiconify()
        self._click_through()
        self.shown = True
        self.last = None
        self.flash_until = time.time() + seconds

    def _click_through(self, catch=False):
        # Окно не забирает фокус у игры. catch=False — и мышь не перехватывает (клики идут в игру);
        # catch=True — ловит клики (только пока курсор над галочкой/кнопкой плашки).
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(self.root.winfo_id())
        GWL_EXSTYLE = -20
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= 0x80000 | 0x80 | 0x08000000  # LAYERED | TOOLWINDOW | NOACTIVATE
        style = (style & ~0x20) if catch else (style | 0x20)   # TRANSPARENT — клики сквозь окно
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        self.catching = catch

    def _zone_at(self, x, y):
        return next((z for z in self.zones if z[0] <= x <= z[2] and z[1] <= y <= z[3]), None)

    def _watch_mouse(self):
        """Раз в 80 мс: курсор над галочкой/кнопкой плашки — окно ловит клик, иначе пропускает в игру."""
        try:
            if self.shown and self.zones:
                px, py = self.root.winfo_pointerxy()
                z = self._zone_at(px - self.root.winfo_rootx(), py - self.root.winfo_rooty())
                if bool(z) != self.catching:
                    self._click_through(catch=bool(z))
            elif self.catching:
                self._click_through(catch=False)
        except Exception:
            log.exception("Плашка: ошибка при слежении за мышью")
        self.root.after(80, self._watch_mouse)

    def _on_click(self, event):
        z = self._zone_at(event.x, event.y)
        if not z:
            return
        changes = z[4]
        self.settings.update(changes)
        log.info("Плашка: нажали %s", json.dumps(changes, ensure_ascii=False))
        try:
            save_settings(changes)
        except Exception:
            log.exception("Не получилось сохранить настройку с плашки")
        self.last = None            # перерисовать сразу
        self.render(self.state)

    def _checkbox(self, c, x, y, on, text, changes, size=9):
        """Галочка с подписью; клик по ней — changes в настройки. Возвращает ширину."""
        box = 13
        if on:      # включено — просто закрашенный квадрат (Макс: без галочек)
            c.create_rectangle(x, y + 1, x + box, y + 1 + box, fill=ACCENT, width=0)
        else:
            c.create_rectangle(x, y + 1, x + box, y + 1 + box, outline="#6d7560", width=1)
        t = c.create_text(x + box + 6, y, text=text, fill=ACCENT if on else DIM, font=(FONT, size, "bold"), anchor="nw")
        x1 = c.bbox(t)[2]
        self.zones.append((x - 3, y - 3, x1 + 3, y + box + 4, changes))
        return x1 - x

    def move_to(self, pos):
        """Встать рядом с меню (когда игра нашлась на другом мониторе / в другом масштабе)."""
        if not self.auto or not pos or pos == (self.x, self.y):
            return
        self.x, self.y = pos
        if self.shown:
            self.root.geometry(f"+{self.x}+{self.y}")

    def render(self, state):
        if not (state and state.get("weapon")) and time.time() < self.flash_until:
            return  # пока висит плашка «запущен»
        key = json.dumps(state, ensure_ascii=False, sort_keys=True)
        if key == self.last:
            return
        self.last = key
        w = state and WEAPONS.get(state.get("weapon"))
        if not w:
            if self.shown:
                self.root.withdraw()
                self.shown = False
                log.debug("Окно: плашка скрыта")
            return
        log.debug("Окно: рисую плашку %s в (%d, %d)", state, self.x, self.y)
        self.state = state
        self.zones = []
        if state.get("ttk"):     # TTK на выбранной кнопкой дистанции (по умолчанию первая кнопка, обычно 50 м)
            buttons, hold = ttk_distances(state["weapon"])
            chosen = (self.settings.get("ttk_dist") or {}).get(state["weapon"])
            dist = chosen if buttons and chosen in buttons else (buttons[0] if buttons else TTK_DIST)
            ttk = ttk_payload(state["weapon"], state.get("tab"), state.get("ammo"), dist)
            if ttk:
                ttk.update(buttons=buttons, hold=hold)
                state = dict(state, ttk=ttk)
                self.state = state
        c = self.canvas
        c.delete("all")
        W, pad = self.W, 16
        c.create_rectangle(0, 0, 4, 2000, fill=ACCENT, width=0)
        y = 14
        view = compare_view(state) if state.get("tab") != "ammo" else None
        c.create_text(pad, y, text=w[0].upper(), fill=TEXT, font=(FONT, 22, "bold"), anchor="nw")
        # «Подробно» — в правом верхнем углу на всех видах (и в сравнении сборок), класс пушки — в строке ниже
        detail = bool(self.settings.get("detail"))
        tw = c.create_text(0, -100, text=T("Подробно", "Details"), font=(FONT, 9, "bold"), anchor="nw")
        width = c.bbox(tw)[2] - c.bbox(tw)[0] + 19
        c.delete(tw)
        self._checkbox(c, W - pad - width, y + 8, detail, T("Подробно", "Details"), {"detail": not detail})
        y += 38
        base = [CLASS_RU.get(w[1], w[1] or "").lower(), w[2] and f"{T('Урон', 'Damage')} {w[2]}",
                w[3] and w[3].replace("rpm", T("в/мин", "rpm")),
                w[4] and f"{T('Дальн.', 'Range')} {w[4].replace(' m', T(' м', ' m'))}"]   # ADS есть в статах ниже
        c.create_text(pad, y, text="  ·  ".join(b for b in base if b), fill=DIM, font=(FONT, 10), anchor="nw")
        y += 26
        c.create_line(pad, y, W - pad, y, fill=LINE)
        y += 8
        if state.get("tab") == "ammo":
            y = self._render_ammo(c, y, state)
        elif view:
            y = self._render_compare(c, y, state, view)
        else:
            y = self._render_plain(c, y, state)
        c.config(height=y)
        self.root.geometry(f"{W}x{y}+{self.x}+{self.y}")
        if not self.shown:
            self.root.deiconify()
            self._click_through()
            self.shown = True

    def _render_atts(self, c, y, names, gone=(), came=()):
        """Список обвесов. gone — уйдут (зачёркнуты), came — придут (оранжевым, со стрелкой)."""
        W, pad = self.W, 16
        if not names and not gone:
            c.create_text(pad, y, text=T("Без обвесов", "No attachments"), fill=DIM, font=(FONT, 11), anchor="nw")
            return y + 22
        for n in list(names) + [g for g in gone if g not in names]:
            a = ATTS.get(n)
            if not a:
                continue
            if n in gone:
                col, font, label = DIM, (FONT, 11, "overstrike"), a[0]
            elif n in came:     # новый обвес — оранжевым (без стрелок, Макс)
                col, font, label = ACCENT, (FONT, 11, "bold"), a[0]
            else:
                col, font, label = TEXT, (FONT, 11), a[0]
            c.create_text(pad, y + 2, text=SLOT_RU.get(a[1], a[1]), fill=DIM, font=(FONT, 8), anchor="nw")
            c.create_text(pad + 78, y, text=label, fill=col, font=font, anchor="nw", width=W - pad * 2 - 78)
            y += 22
        return y

    def _render_compare(self, c, y, state, view):
        """Сравнение с запомненной сборкой: колонки БЫЛО / СЕЙЧАС (сейчас зелёным — лучше, красным — хуже),
        «Подробно» и «Оружейник» — как на обычной плашке (колонки СОВЕТ тут нет — иначе три колонки цифр)."""
        W, pad = self.W, 16
        old, new, gone, came = view
        cur = list(state.get("attachments", []))
        head, col_old, col_new = T("С ЗАПОМНЕННОЙ СБОРКОЙ", "VS SAVED BUILD"), T("БЫЛО", "WAS"), T("СЕЙЧАС", "NOW")
        y = self._render_atts(c, y, cur, gone, came)
        y = self._render_ammo_line(c, y, state.get("ammo"))
        y += 4
        c.create_line(pad, y, W - pad, y, fill=LINE)
        y += 8
        c.create_text(pad, y, text=head, fill=ACCENT, font=(FONT, 7, "bold"), anchor="nw")
        x_old, x_new = W - pad - 84, W - pad
        c.create_text(x_old, y, text=col_old, fill=DIM, font=(FONT, 7, "bold"), anchor="ne")
        c.create_text(x_new, y, text=col_new, fill=DIM, font=(FONT, 7, "bold"), anchor="ne")
        y += 15
        only = None if self.settings.get("detail") else KEY_STATS
        rows = compute_compare(old, new, state.get("weapon"), only)
        if not rows:
            c.create_text(pad, y, text=T("Обвесы не меняют статы", "Attachments don't change stats"), fill=DIM,
                          font=(FONT, 11), anchor="nw")
            y += 24
        for label, was, now, verdict in rows:
            col = GOOD if verdict > 0 else BAD if verdict < 0 else DIM
            c.create_text(pad, y, text=label, fill=TEXT, font=(FONT, 11), anchor="nw")
            c.create_text(x_old, y + 1, text=was, fill=DIM, font=(FONT, 10), anchor="ne")
            c.create_text(x_new, y, text=now, fill=col, font=(FONT, 11, "bold"), anchor="ne")
            y += 24
        smith = bool(self.settings.get("gunsmith"))
        mode = int(self.settings.get("gunsmith_mode") or 0) % len(GUNSMITH_MODES)
        advice = gunsmith_advice(state.get("weapon"), cur, mode)[0] if smith else []
        y = self._render_gunsmith(c, y, advice, smith, mode)
        y = self._render_ttk(c, y, state.get("ttk"))
        y += 4
        hint = f"{COMPARE_KEY['text']} — " + T("сбросить сравнение", "reset comparison")
        c.create_text(pad, y, text=T("Данные: metaforge.app  ·  ", "Data: metaforge.app  ·  ") + hint, fill=DIM, font=(FONT, 7), anchor="nw")
        return y + 18

    def _render_ammo(self, c, y, state):
        """Вкладка «Боеприпасы»: магазин, выбранные патроны и что они дают, TTK по видам патронов."""
        W, pad = self.W, 16
        weapon = state.get("weapon")
        has_mags = any(weapon in a[3] and a[1] in MAG_TYPES for a in ATTS.values())
        ttk = state.get("ttk") or {}
        one_kind = len(ttk.get("rows") or []) <= 1      # лук, дробовик: патроны (стрелы) одного вида
        y = self._render_atts(c, y, state.get("attachments", [])) if state.get("attachments") else y
        if not state.get("attachments") and has_mags:
            c.create_text(pad, y, text=T("Магазин ещё не выбран", "No magazine chosen yet"), fill=DIM, font=(FONT, 11), anchor="nw")
            y += 22
        ammo = state.get("ammo")
        if ammo or not one_kind:
            c.create_text(pad, y + 2, text=T("ПАТРОНЫ", "AMMO"), fill=DIM, font=(FONT, 8), anchor="nw")
            shotgun = (WEAPONS.get(weapon) or [None, None])[1] == "Shotgun"
            name = T("картечь", "buckshot") if shotgun and ammo == "FMJ" else AMMO_RU.get(ammo, T("ещё не выбраны", "not chosen yet"))
            c.create_text(pad + 78, y, text=name, fill=TEXT if ammo else DIM,
                          font=(FONT, 11), anchor="nw")
            y += 22
        if ammo in AMMO_NOTE:
            c.create_text(pad + 78, y - 2, text=AMMO_NOTE[ammo], fill=DIM, font=(FONT, 9), anchor="nw",
                          width=W - pad * 2 - 78)
            y += 18
        y += 2
        c.create_line(pad, y, W - pad, y, fill=LINE)
        y += 8
        y = self._render_stats_smith(c, y, state)     # статы и оружейник — уже на первой вкладке
        y = self._render_ttk(c, y, state.get("ttk"))
        y += 4
        c.create_text(pad, y, text=T("Данные: metaforge.app", "Data: metaforge.app"), fill=DIM, font=(FONT, 7), anchor="nw")
        return y + 18

    def _render_ttk(self, c, y, ttk):
        """Блок TTK: сколько попаданий и секунд до убийства (строки × без брони / броня 2 / броня 4).
        Выбранная строка (патроны на вкладке «Боеприпасы») — оранжевая со стрелкой."""
        if not ttk or not ttk.get("rows"):
            return y
        W, pad = self.W, 16
        y += 2
        c.create_line(pad, y, W - pad, y, fill=LINE)
        y += 8
        title = ttk["title"]   # дистанция видна на кнопках, а если урон держит — она не важна
        c.create_text(pad, y, text=title, fill=ACCENT, font=(FONT, 8, "bold"), anchor="nw")
        if ttk.get("buttons"):           # кнопки дистанции — кликабельные, выбранная оранжевая
            bx = W - pad
            weapon = (self.state or {}).get("weapon")
            for d in reversed(ttk["buttons"]):
                t = c.create_text(0, -100, text=f"{d}" + T(" м", " m"), font=(FONT, 8, "bold"), anchor="nw")
                tw = c.bbox(t)[2] - c.bbox(t)[0] + 12
                c.delete(t)
                x0 = bx - tw
                if d == ttk.get("dist"):
                    c.create_rectangle(x0, y - 2, bx, y + 14, fill=ACCENT, width=0)
                    c.create_text((x0 + bx) / 2, y, text=f"{d}" + T(" м", " m"), fill=BG, font=(FONT, 8, "bold"), anchor="n")
                else:
                    c.create_rectangle(x0, y - 2, bx, y + 14, outline="#5b6250", width=1)
                    c.create_text((x0 + bx) / 2, y, text=f"{d}" + T(" м", " m"), fill=DIM, font=(FONT, 8, "bold"), anchor="n")
                    dists = dict(self.settings.get("ttk_dist") or {})
                    dists[weapon] = d
                    self.zones.append((x0 - 2, y - 4, bx + 2, y + 16, {"ttk_dist": dists}))
                bx = x0 - 4
            y += 4
        elif ttk.get("hold"):
            c.create_text(W - pad, y, text=T(f"Урон держит до {ttk['hold']} м", f"Damage holds up to {ttk['hold']} m"), fill=GOOD, font=(FONT, 8, "bold"),
                          anchor="ne")
        y += 16
        # колонки (без брони, броня 1…4): крупно — сколько попаданий, под ним мелко — секунды
        x0, col_w = 92, (W - pad - 92) / len(ttk["cols"])
        for i, name in enumerate(ttk["cols"]):
            c.create_text(x0 + col_w * (i + 0.5), y, text=name, fill=DIM, font=(FONT, 7), anchor="n")
        y += 14
        fastest = [min((r[1][i][1] for r in ttk["rows"] if r[1][i][1] is not None), default=None)
                   for i in range(len(ttk["cols"]))]
        for label, cells, chosen in ttk["rows"]:
            c.create_text(pad, y + 6, text=("▶ " if chosen else "") + label, fill=ACCENT if chosen else TEXT,
                          font=(FONT, 11, "bold" if chosen else "normal"), anchor="nw")
            for i, (shots, secs) in enumerate(cells):
                mid = x0 + col_w * (i + 0.5)
                if shots is None:
                    c.create_text(mid, y + 6, text="—", fill=DIM, font=(FONT, 11), anchor="n")
                    continue
                top = ttk.get("best") and secs == fastest[i]
                c.create_text(mid, y, text=f"{shots}×", fill=GOOD if top else TEXT, font=(FONT, 12, "bold"), anchor="n")
                c.create_text(mid, y + 19, text=T(f"{secs:.2f} с".replace(".", ","), f"{secs:.2f} s"), fill=GOOD if top else DIM,
                              font=(FONT, 8), anchor="n")
            y += 36
        if ttk.get("note"):     # строка без цифр (нарезная пуля у дробовиков)
            c.create_text(pad, y + 2, text=ttk["note"], fill=DIM, font=(FONT, 10), anchor="nw")
            y += 24
        return y

    def _render_ammo_line(self, c, y, ammo):
        """Строка «ПАТРОНЫ …» под обвесами (если патроны известны)."""
        if ammo not in AMMO_RU:
            return y
        c.create_text(16, y + 2, text=T("ПАТРОНЫ", "AMMO"), fill=DIM, font=(FONT, 8), anchor="nw")
        c.create_text(16 + 78, y, text=ammo_name((self.state or {}).get("weapon"), ammo), fill=TEXT, font=(FONT, 11),
                      anchor="nw")
        return y + 22

    def _render_gunsmith(self, c, y, advice, on, mode):
        """Блок «Оружейник рекомендует»: галочка вкл/выкл, кнопки режима, советуемые обвесы
        (поставленный — зелёный с галочкой, стоит другой — оранжевое «сейчас: …»)."""
        W, pad = self.W, 16
        y += 2
        c.create_line(pad, y, W - pad, y, fill=LINE)
        y += 9
        self._checkbox(c, pad, y, on, T("ОРУЖЕЙНИК РЕКОМЕНДУЕТ", "GUNSMITH SUGGESTS"), {"gunsmith": not on})
        if not on:
            return y + 22
        bx = W - pad
        for i in reversed(range(len(GUNSMITH_MODES))):
            name = GUNSMITH_MODES[i][0]
            t = c.create_text(0, -100, text=name, font=(FONT, 8, "bold"), anchor="nw")
            tw = c.bbox(t)[2] - c.bbox(t)[0] + 12
            c.delete(t)
            x0 = bx - tw
            if i == mode:
                c.create_rectangle(x0, y - 1, bx, y + 16, fill=ACCENT, width=0)
                c.create_text((x0 + bx) / 2, y + 1, text=name, fill=BG, font=(FONT, 8, "bold"), anchor="n")
            else:
                c.create_rectangle(x0, y - 1, bx, y + 16, outline="#5b6250", width=1)
                c.create_text((x0 + bx) / 2, y + 1, text=name, fill=DIM, font=(FONT, 8, "bold"), anchor="n")
                self.zones.append((x0 - 2, y - 3, bx + 2, y + 18, {"gunsmith_mode": i}))
            bx = x0 - 4
        y += 24
        for label, best, cur in advice:
            ok = cur == best
            c.create_text(pad, y + 2, text=label, fill=DIM, font=(FONT, 8), anchor="nw")
            x = pad + 78
            if ok:
                c.create_line(x, y + 8, x + 4, y + 12, x + 11, y + 3, fill=GOOD, width=2)
                x += 16
            c.create_text(x, y, text=best, fill=GOOD if ok else TEXT, font=(FONT, 11, "bold" if ok else "normal"),
                          anchor="nw", width=W - pad - x)
            y += 21
            if cur and not ok:
                c.create_text(pad + 78, y - 2, text=T(f"сейчас: {cur}", f"now: {cur}"), fill=ACCENT, font=(FONT, 9), anchor="nw",
                              width=W - pad * 2 - 78)
                y += 17
        if not advice:
            c.create_text(pad + 78, y, text=T("для этой пушки советовать нечего", "nothing to suggest for this weapon"), fill=DIM, font=(FONT, 9), anchor="nw")
            y += 18
        c.create_text(pad + 78, y, text=T("прицел и магазин — на твой вкус", "sight and magazine — your choice"), fill=DIM, font=(FONT, 8), anchor="nw")
        return y + 18

    def _render_plain(self, c, y, state):
        W, pad = self.W, 16
        names = state.get("attachments", [])
        y = self._render_atts(c, y, names)
        y = self._render_ammo_line(c, y, state.get("ammo"))
        y += 4
        c.create_line(pad, y, W - pad, y, fill=LINE)
        y += 8
        y = self._render_stats_smith(c, y, state)
        y = self._render_ttk(c, y, state.get("ttk"))
        y += 4
        hint = (T(f"сборка запомнена ({COMPARE_KEY['text']} — сбросить)", f"build saved ({COMPARE_KEY['text']} — reset)")
                if state.get("saved") is not None
                else T(f"{COMPARE_KEY['text']} — запомнить сборку для сравнения",
                       f"{COMPARE_KEY['text']} — save build to compare"))
        c.create_text(pad, y, text=T("Данные: metaforge.app  ·  ", "Data: metaforge.app  ·  ") + hint, fill=DIM, font=(FONT, 7), anchor="nw")
        return y + 18

    def _render_stats_smith(self, c, y, state):
        """Статистика (текущая или «С СОВЕТОМ») + блок оружейника. Общий для «Модулей» и «Боеприпасов»:
        игрок ещё на первой вкладке видит, что купить и что это даст."""
        W, pad = self.W, 16
        names = state.get("attachments", [])
        smith = bool(self.settings.get("gunsmith"))
        mode = int(self.settings.get("gunsmith_mode") or 0) % len(GUNSMITH_MODES)
        only = None if self.settings.get("detail") else KEY_STATS
        if smith:
            # Статы — всегда того, что стоит (поменял обвес — цифры сразу меняются); оружейник добавляет
            # в ту же строку оранжевое «→ сколько станет с советом» и отметку-цель на полоске.
            advice, advised = gunsmith_advice(state.get("weapon"), names, mode)
            y = self._render_pair_rows(c, y, compute_pair_rows(names, advised, state.get("weapon"), only))
            return self._render_gunsmith(c, y, advice, smith, mode)
        advice = []
        rows = compute_rows(names, state.get("weapon"), only)
        if not rows:
            c.create_text(pad, y, text=T("Обвесы не меняют статы", "Attachments don't change stats"), fill=DIM,
                          font=(FONT, 11), anchor="nw")
            y += 24
        bar_x0, bar_w = 200, 110
        for ru, better, m, value in rows:
            c.create_text(pad, y, text=ru, fill=TEXT, font=(FONT, 11), anchor="nw")
            if better is None:
                c.create_text(W - pad, y, text=f"{m:.1f}×".replace(".0×", "×"), fill=TEXT, font=(FONT, 11, "bold"), anchor="ne")
            elif abs(m - 1) < 0.005:      # главный стат, который обвесы не меняют (без «Подробно» показываем всегда)
                c.create_text(W - pad, y, text=value or "±0%", fill=DIM, font=(FONT, 11, "bold"), anchor="ne")
            else:
                pct = round((m - 1) * 100)
                good = pct < 0 if better == "low" else pct > 0
                col = GOOD if good else BAD
                mid = bar_x0 + bar_w / 2
                by = y + 10
                c.create_rectangle(bar_x0, by - 3, bar_x0 + bar_w, by + 3, fill="#25281f", width=0)
                ln = min(bar_w / 2, abs(pct) * bar_w / 80)
                if good:
                    c.create_rectangle(mid, by - 3, mid + ln, by + 3, fill=col, width=0)
                else:
                    c.create_rectangle(mid - ln, by - 3, mid, by + 3, fill=col, width=0)
                c.create_line(mid, by - 5, mid, by + 5, fill="#777")
                sign = "+" if pct > 0 else "−"
                c.create_text(W - pad, y, text=value or f"{sign}{abs(pct)}%", fill=col, font=(FONT, 11, "bold"),
                              anchor="ne")
            y += 24
        return self._render_gunsmith(c, y, advice, smith, mode)

    def _render_pair_rows(self, c, y, rows):
        """Строки статов при включённом оружейнике: справа — сейчас (цветом), левее оранжевым «→ с советом»
        (только если совет что-то меняет), на полоске — текущее значение и оранжевая отметка, куда приведёт совет."""
        W, pad = self.W, 16
        x_adv = W - pad - 64                     # правый край колонки «с советом»
        c.create_text(x_adv, y, text=T("СОВЕТ", "SUGGESTED"), fill=ACCENT, font=(FONT, 7, "bold"), anchor="ne")
        c.create_text(W - pad, y, text=T("СЕЙЧАС", "NOW"), fill=DIM, font=(FONT, 7, "bold"), anchor="ne")
        y += 15
        bar_x0, bar_w = 184, 50              # узкая полоска: слева длинные подписи, справа две колонки цифр
        mid = bar_x0 + bar_w / 2

        def text(m, val):
            if val:
                return val
            pct = round((m - 1) * 100)
            return f"{'+' if pct > 0 else '−' if pct < 0 else '±'}{abs(pct)}%"

        def colour(m, better):
            if abs(m - 1) < 0.005:
                return DIM
            return GOOD if (m < 1) == (better == "low") else BAD

        for label, better, a, va, b, vb in rows:
            c.create_text(pad, y, text=label, fill=TEXT, font=(FONT, 11), anchor="nw")
            by = y + 10
            c.create_rectangle(bar_x0, by - 3, bar_x0 + bar_w, by + 3, fill="#25281f", width=0)
            # полоска: «лучше» — вправо от середины, «хуже» — влево (как в обычной плашке)
            sa = (a - 1) * 100 * (-1 if better == "low" else 1)
            la = max(-bar_w / 2, min(bar_w / 2, sa * bar_w / 60))
            if abs(la) >= 0.5:
                c.create_rectangle(min(mid, mid + la), by - 3, max(mid, mid + la), by + 3, fill=colour(a, better),
                                   width=0)
            c.create_line(mid, by - 5, mid, by + 5, fill="#777")
            differs = text(a, va) != text(b, vb)
            if differs:
                sb = (b - 1) * 100 * (-1 if better == "low" else 1)
                xb = mid + max(-bar_w / 2, min(bar_w / 2, sb * bar_w / 60))
                c.create_rectangle(xb - 1, by - 7, xb + 1, by + 7, fill=ACCENT, width=0)   # цель совета
                c.create_text(x_adv, y + 1, text=text(b, vb), fill=ACCENT, font=(FONT, 10, "bold"), anchor="ne")
            else:       # совет этот стат не меняет — то же число серым, чтобы клетка не казалась ошибкой
                c.create_text(x_adv, y + 1, text=text(b, vb), fill=DIM, font=(FONT, 10), anchor="ne")
            c.create_text(W - pad, y, text=text(a, va), fill=colour(a, better), font=(FONT, 11, "bold"), anchor="ne")
            y += 24
        if not rows:
            c.create_text(pad, y, text=T("Обвесы не меняют статы", "Attachments don't change stats"), fill=DIM,
                          font=(FONT, 11), anchor="nw")
            y += 24
        return y


# ---------------------------------------------------------------- главный цикл
PLACEMENT = {"pos": None}   # где сейчас должно стоять окно (заполняет capture_loop)
SCAN = {"active": threading.Event()}   # идёт ли поиск меню (иначе программа спит)
STATUS = {"game": None, "menu": False, "shown": None}   # для строки состояния в консольном меню
UI_MESSAGES = []            # короткие плашки для окна («поиск включён» и т.п.) — из других потоков
SAVED = {}                  # пушка → запомненная сборка (для сравнения; до закрытия программы)
COMPARE_KEY = {"text": "Ctrl+Shift+S"}   # клавиша «запомнить сборку» (из настроек)
FULL_SEARCH_EVERY = 10.0    # раз в столько секунд — полный поиск по всем масштабам (0.3–0.5 с на 1080p–1440p)
IDLE_INTERVAL = 1.0         # когда меню не видно — смотреть раз в секунду
GAME_EXE = "wardogsclient"  # процесс игры: WardogsClient-Win64-Shipping.exe
GAME_TITLE = "Wardogs"      # заголовок окна игры (если имя процесса не прочитать)


class GameWindow:
    """Находит окно игры через Windows и говорит, где на экране его картинка.
    Смотрим только на него — OBS, скриншоты и прочие окна с картинкой игры не мешают."""

    def __init__(self):
        from ctypes import wintypes
        self.wt = wintypes
        self.u = ctypes.windll.user32
        self.k = ctypes.windll.kernel32
        self.hwnd = None
        self._looked_at = 0.0

    def _exe(self, hwnd):
        pid = self.wt.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = self.k.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None                                     # античит может не пустить — тогда по заголовку
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = self.wt.DWORD(1024)
            if self.k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            self.k.CloseHandle(h)
        return None

    def _title(self, hwnd):
        n = self.u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        self.u.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def find(self):
        if self.hwnd and self.u.IsWindow(self.hwnd):
            return self.hwnd
        if self.hwnd:
            log.info("Окно игры закрылось (hwnd=%s)", self.hwnd)
        self.hwnd = None
        if time.time() - self._looked_at < 2:               # искать окно — не чаще раза в 2 секунды
            return None
        self._looked_at = time.time()
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, self.wt.HWND, self.wt.LPARAM)
        def each(hwnd, _):
            if self.u.IsWindowVisible(hwnd):
                exe = self._exe(hwnd)
                if (exe and exe.startswith(GAME_EXE)) or (exe is None and self._title(hwnd) == GAME_TITLE):
                    found.append(hwnd)
            return True

        self.u.EnumWindows(each, 0)
        if found:
            self.hwnd = found[0]
            log.info("Нашёл окно игры: hwnd=%s, процесс=%s, заголовок=%r (окон-кандидатов: %d)",
                     self.hwnd, self._exe(self.hwnd), self._title(self.hwnd), len(found))
        return self.hwnd

    def rect(self):
        """Картинка игры на экране: {"left","top","width","height"} или None (свёрнута/нет)."""
        hwnd = self.find()
        if not hwnd or self.u.IsIconic(hwnd):
            return None
        r = self.wt.RECT()
        self.u.GetClientRect(hwnd, ctypes.byref(r))
        p = self.wt.POINT(0, 0)
        self.u.ClientToScreen(hwnd, ctypes.byref(p))
        if r.right < 200 or r.bottom < 150:
            return None
        return {"left": p.x, "top": p.y, "width": r.right, "height": r.bottom}

    def owns_point(self, x, y):
        """Это место экрана действительно показывает игру (а не окно поверх неё)?"""
        hwnd = self.u.WindowFromPoint(self.wt.POINT(int(x), int(y)))
        return bool(hwnd) and self.u.GetAncestor(hwnd, 2) == self.hwnd   # GA_ROOT


def capture_loop(settings, rec, stop, fake=None):
    import mss
    loc = Locator()
    game = GameWindow()
    manual_monitor = settings["monitor"] if isinstance(settings["monitor"], int) else None
    sct_class = getattr(mss, "MSS", None) or mss.mss
    with sct_class() as sct:
        fake_img = read_rgb(fake) if fake else None

        def area():
            """Что снимать: окно игры (обычно), монитор из настроек, или картинку для проверки."""
            if fake_img is not None:
                return {"left": 0, "top": 0, "width": fake_img.shape[1], "height": fake_img.shape[0]}
            if manual_monitor and 1 <= manual_monitor < len(sct.monitors):
                return sct.monitors[manual_monitor]
            return game.rect()

        def grab(a):
            if fake_img is not None:
                return fake_img
            return np.ascontiguousarray(np.array(sct.grab(a))[..., :3][..., ::-1])  # BGRA → RGB

        log.info("Мониторы: %s", [(m["left"], m["top"], m["width"], m["height"]) for m in sct.monitors[1:]])
        log.info("Что снимаю: %s", "картинку " + fake if fake else
                 f"монитор {manual_monitor} (задан в настройках)" if manual_monitor else "окно игры")
        where = None          # (s, x, y) — где меню нашлось в прошлый раз (внутри снимка)
        last_scale = None
        last_full = 0.0
        last_print = None
        last_area = "нет"     # для журнала: что изменилось
        covered = False
        debug_saved = False   # debug_miss.jpg — не чаще раза за запуск
        stats = {"ticks": 0, "busy": 0.0, "menu": 0, "max": 0.0}
        last_beat = time.time()
        proc_cpu0 = time.process_time()
        auto_off = float(settings.get("auto_off_seconds", 10))
        confirm = max(1, int(settings.get("confirm_frames", 2)))
        shown, candidate, cand_count = "null", None, 0   # подтверждение по нескольким кадрам
        was_active = False
        menu_seen_at = time.time()
        while not stop.is_set():
            auto_off = float(settings.get("auto_off_seconds", 10))   # меняется из пульта на ходу
            # ---- сон: поиск выключен (через auto_off секунд без меню или горячей клавишей)
            if not SCAN["active"].is_set():
                if was_active:
                    was_active = False
                    where = None
                    server.set_state(None, [])
                    shown, candidate, cand_count = "null", None, 0
                    STATUS["shown"], STATUS["menu"] = None, False
                    log.info("Поиск выключен — сплю. Включить: %s", settings.get("hotkey"))
                SCAN["active"].wait(1.0)
                continue
            if not was_active:
                was_active = True
                menu_seen_at = time.time()
                last_full = 0.0                       # сразу полный поиск
                log.info("Поиск включён (без меню «Модули» засну через %.0f с)", auto_off)
            t = time.time()
            state = None
            try:
                found, a = None, area()
                STATUS["game"] = a is not None
                if a != last_area:
                    log.info("Картинка игры на экране: %s", a if a else "нет (игра не запущена или свёрнута)")
                    if a and not isinstance(last_area, dict):
                        ev(T("Игра найдена", "Game found"), "ok")
                    elif not a:
                        ev(T("Игра не найдена (не запущена или свёрнута)", "Game not found (not running or minimized)"), "warn")
                    last_area = a
                if a is None:
                    where = None                      # игры нет или свёрнута
                else:
                    frame = grab(a)
                    if where:                         # быстро: меню там же, где было?
                        was_mode = loc.mode
                        hit = loc.check_any(_gray(frame), *where, frame_rgb=frame)
                        if hit:
                            found = hit[1:]
                            if loc.mode != was_mode:
                                log.info("Вкладка: %s", TAB_RU[loc.mode])
                                ev(T("Открыта вкладка «", "Tab opened: «") + TAB_RU[loc.mode] + "»", "dim")
                        else:
                            log.info("Меню «Модули» пропало")
                            ev(T("Меню «Модули» закрыто", "ATTC. menu closed"), "dim")
                    if not found:
                        # меню не видно — самый вероятный масштаб; полный перебор (игра в окне)
                        # — раз в FULL_SEARCH_EVERY секунд, чтобы не грузить процессор
                        h, w = frame.shape[:2]
                        scales = default_scales(h, w)
                        full = t - last_full >= FULL_SEARCH_EVERY
                        if full:
                            last_full = t
                        else:
                            scales = [last_scale] if last_scale else scales[:1]
                        t_search = time.time()
                        found = loc.search(_gray(frame), scales, frame)
                        if found:
                            log.info("Меню найдено (вкладка «%s»): масштаб %.3f, надпись в (%d, %d) внутри снимка "
                                     "%dx%d; поиск %s за %.2f с", TAB_RU[loc.mode], found[0], found[1], found[2], w, h,
                                     "полный" if full else f"по масштабу {scales}", time.time() - t_search)
                            ev(T("Меню открыто — вкладка «", "Menu open — tab «") + TAB_RU[loc.mode] + "»", "ok")
                        elif full:
                            # чтобы по журналу было понятно, ПОЧЕМУ не нашлось: надпись совсем не похожа
                            # (другой язык / другое меню) или похожа, но не дотянула (масштаб, размытие)
                            log.debug("Полный поиск (%d масштабов, %.2f с): меню не найдено. Грубо лучшее совпадение "
                                      "%.3f на масштабе %s (нужно ≥0.55); ближе всего: %s (нужно: заголовок ≥%.2f, "
                                      "подтверждение ≥%.2f, сумма ≥%.2f)", len(scales), time.time() - t_search,
                                      loc.coarse[0], loc.coarse[1], loc.near or "—", HEADER_MIN, LABEL_MIN, PAIR_MIN)
                            # «почти нашлось» — один раз за запуск сохраняем картинку окна игры для разбора
                            near = loc.near or {}
                            if not debug_saved and near.get("заголовок", 0) >= 0.6 and (near.get("подтверждение") or 0) >= 0.35:
                                debug_saved = True
                                try:
                                    k = min(1.0, 1280 / w)
                                    small = cv2.resize(frame, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
                                    save_jpg(os.path.join(HERE, "debug_miss.jpg"), small, 85)
                                    log.info("Меню почти нашлось, но не дотянуло — сохранил картинку окна игры "
                                             "в debug_miss.jpg (для разбора): %s", near)
                                except Exception:
                                    log.exception("Не смог сохранить debug_miss.jpg")
                    if found and fake_img is None and manual_monitor is None:
                        s, hx, hy = found
                        # поверх игры может быть открыто другое окно (скриншот с инвентарём и т.п.)
                        if not game.owns_point(a["left"] + hx + 50 * s, a["top"] + hy + 11 * s):
                            if not covered:
                                log.info("Меню видно, но поверх игры другое окно — плашку не показываю")
                                ev(T("Поверх игры другое окно — плашку не показываю", "Another window covers the game — panel hidden"), "warn")
                            covered, found = True, None
                        elif covered:
                            log.info("Игра снова сверху")
                            covered = False
                    where = found
                if found:
                    s, hx, hy = found
                    last_scale = s
                    pos = placement_for(a, s, hx, hy)
                    if pos != PLACEMENT["pos"]:
                        log.debug("Плашка встанет в %s", pos)
                    PLACEMENT["pos"] = pos
                    t_an = time.time()
                    state = rec.analyze(to_reference(frame, s, hx, hy), native=(frame, s, hx, hy), mode=loc.mode)
                    if time.time() - t_an > 0.3:
                        log.debug("Распознавание кадра заняло %.2f с", time.time() - t_an)
                    stats["menu"] += 1
            except Exception:
                log.exception("Ошибка распознавания")
                ev(T("Ошибка распознавания — подробности в журнале", "Recognition error — see overlay.log"), "err")
                state = None
            STATUS["menu"] = bool(where)
            # ---- автоотключение: меню давно не видно — засыпаем, чтобы не тратить процессор
            if where:
                menu_seen_at = time.time()
            elif auto_off > 0 and time.time() - menu_seen_at >= auto_off:
                log.info("Меню «Модули» не видно %.0f с — выключаю поиск", auto_off)
                ev(T(f"Поиск уснул ({auto_off:.0f} с без меню)", f"Search paused ({auto_off:.0f} s without the menu)"), "dim")
                SCAN["active"].clear()
                continue
            # ---- показываем, только если одно и то же увидели confirm кадров подряд
            # (не мигает во время анимаций и не принимает случайный кадр)
            raw = json.dumps(state if state and state.get("weapon") else None, ensure_ascii=False, sort_keys=True)
            if raw == shown:
                candidate, cand_count = None, 0
            elif raw == candidate:
                cand_count += 1
            else:
                candidate, cand_count = raw, 1
            if candidate is not None and cand_count >= confirm:
                log.debug("Подтверждено %d кадрами подряд: %s", cand_count, candidate)
                shown, candidate, cand_count = candidate, None, 0
                st = json.loads(shown)
                STATUS["shown"] = st
                publish(st)
                if st:
                    ev(describe_build(st), "ok")
            if shown != last_print:
                log.info("Показываю: %s", shown)
                last_print = shown
            busy = time.time() - t
            stats["ticks"] += 1
            stats["busy"] += busy
            stats["max"] = max(stats["max"], busy)
            if time.time() - last_beat >= 60:   # раз в минуту — «жив» и сколько ресурсов уходит
                cpu = (time.process_time() - proc_cpu0) / (time.time() - last_beat) * 100
                log.info("Работаю: за минуту %d кадров (меню видно в %d), среднее %.0f мс, макс %.0f мс, "
                         "процессор ~%.1f%% одного ядра", stats["ticks"], stats["menu"],
                         stats["busy"] / max(1, stats["ticks"]) * 1000, stats["max"] * 1000, cpu)
                stats = {"ticks": 0, "busy": 0.0, "menu": 0, "max": 0.0}
                last_beat, proc_cpu0 = time.time(), time.process_time()
            pause = settings["interval"] if where else max(settings["interval"], IDLE_INTERVAL)
            stop.wait(max(0.1, pause - (time.time() - t)))


def _ver(text):
    return tuple(int(x) for x in text.split("."))


def check_update(on_new):
    """В фоне: открыть руководство в Steam и найти там самую новую «Обновление X.Y». Новее нашей —
    on_new(версия). Ничего не скачивает — только сообщает. Нет интернета — тихо пишет в журнал."""
    def run():
        import urllib.request
        t = time.time()
        try:
            req = urllib.request.Request(GUIDE_URL, headers={"User-Agent": "Mozilla/5.0 WardogsOverlay/" + VERSION,
                                                             "Accept-Language": "ru"})
            html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
            found = sorted(set(re.findall(r"Обновление\s+(\d+(?:\.\d+)+)", html)), key=_ver)
            log.info("Проверка обновлений: в руководстве версии %s, у меня %s (%.1f с)",
                     found or "не нашёл", VERSION, time.time() - t)
            if found and _ver(found[-1]) > _ver(VERSION):
                ev(T(f"Вышла новая версия {found[-1]} — скачать по ссылке в руководстве Steam",
                      f"New version {found[-1]} is out — download link in the Steam guide"), "warn")
                on_new(found[-1])
        except Exception as e:
            log.warning("Проверка обновлений не получилась (нет интернета?): %s", e)

    threading.Thread(target=run, daemon=True, name="обновления").start()


def update_db_now(rec):
    """Скачать свежую базу с MetaForge и сразу начать ею пользоваться. → (получилось?, что сказать)."""
    t = time.time()
    ev(T("Обновляю базу с MetaForge…", "Updating data from MetaForge…"), "dim")
    r = subprocess.run(updater_cmd(), cwd=HERE, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", creationflags=0x08000000)   # CREATE_NO_WINDOW
    if r.returncode != 0:
        log.error("Обновление базы не удалось (код %s): %s", r.returncode, (r.stdout or "")[-2000:])
        ev(T("Обновить базу не получилось — старая не тронута", "Data update failed — old data kept"), "err")
        return False, T("Не получилось — старая база не тронута. Подробности в update_database.log",
                        "Failed — old data kept. Details in update_database.log")
    n_w, n_a = reload_db()
    rec._tpl_cache.clear()
    rec._miss.clear()
    m = re.search(r"изменёнными статами: (\d+)", r.stdout or "")
    changed = m.group(1) if m else "?"
    log.info("База обновлена за %.1f с", time.time() - t)
    ev(T(f"База обновлена: {n_w} пушек, {n_a} обвесов, изменено {changed}",
          f"Data updated: {n_w} weapons, {n_a} attachments, {changed} changed"), "ok")
    return True, T(f"{n_w} пушек, {n_a} обвесов, изменилось {changed}",
                   f"{n_w} weapons, {n_a} attachments, {changed} changed")


MANUAL = "ИНСТРУКЦИЯ.txt"
MANUAL_EN = "MANUAL.txt"


def open_manual():
    """Открыть инструкцию (кнопка в настройках) на языке программы. Рядом с программой её нет (удалили) —
    кладём туда копию, спрятанную внутри .exe."""
    MANUAL = T(globals()["MANUAL"], MANUAL_EN)
    here = os.path.join(HERE, MANUAL)
    try:
        if not os.path.exists(here):
            for src in (os.path.join(BUNDLE, MANUAL), os.path.join(os.path.dirname(HERE), MANUAL)):
                if os.path.exists(src):
                    if FROZEN:
                        import shutil
                        shutil.copyfile(src, here)
                        log.info("Инструкции рядом не было — положил копию из программы")
                    else:
                        here = src            # обычный запуск: инструкция лежит в папке выше app
                    break
        log.info("Открываю инструкцию: %s", here)
        os.startfile(here)
    except OSError as e:
        log.exception("Не смог открыть инструкцию")
        message(T(f"Не смог открыть инструкцию: {e}", f"Could not open the manual: {e}"))


TWITCH_URL = "https://www.twitch.tv/alien_name"
TELEGRAM_URL = "https://t.me/maxvereshchagin"
DONATE_URL = "https://www.donationalerts.com/r/alienname"


def settings_window(root, settings, on_restart, win=None, ctx=None):
    """«Пульт» (1.8, вид согласован с Максом по картинке mockups/6_пульт_цвета.png, цвета как у плашки):
    шапка с RU/EN, плитки состояния (поиск, игра, плашка, база), кнопки, карточки «Плашка», «Клавиши в игре»,
    «Разное», ссылки внизу. Что можно — меняется сразу (непрозрачность, оружейник, «Подробно», засыпание);
    клавиши, OBS, монитор, «показывать у меня» — сохраняются и просят перезапуск (кнопка внизу);
    язык — перезапуск сразу. settings — живой словарь программы (тот же, что у плашки и поиска)."""
    import tkinter as tk
    from tkinter import ttk
    s = settings
    ctx = ctx if ctx is not None else {"rec": None, "updating": False}
    PANEL, RAISED, ROWLINE, FAINT, OFF, KEY = "#171a13", "#20241a", "#23271d", "#6d7560", "#2a2e22", "#4b5240"
    SEMI = "Bahnschrift SemiBold"
    top = tk.Toplevel(root)
    top.title(f"Wardogs Overlay {VERSION}")
    top.configure(bg=BG)
    top.resizable(False, False)
    top.attributes("-topmost", True)
    try:
        top.iconbitmap(ICON_FILE)
        # Tk отдаёт панели задач маленький значок 16×16 и Windows его растягивает — «мыльно» (Макс).
        # Ставим окну значки нужных размеров сами: большой (панель задач, Alt+Tab) и маленький (заголовок).
        top.update_idletasks()
        u = ctypes.windll.user32
        u.LoadImageW.restype = ctypes.c_void_p
        u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p]
        hwnd = u.GetParent(top.winfo_id())
        for which, metric in ((1, 11), (0, 49)):     # ICON_BIG ← SM_CXICON, ICON_SMALL ← SM_CXSMICON
            size = u.GetSystemMetrics(metric)
            h = u.LoadImageW(None, ICON_FILE, 1, size, size, 0x10)   # IMAGE_ICON, LR_LOADFROMFILE
            if h:
                u.SendMessageW(hwnd, 0x80, which, h)                  # WM_SETICON
                _handles.append(("icon", h))
    except Exception:
        log.exception("Пульт: не поставил значок окна")
    st = ttk.Style(top)
    st.theme_use("clam")
    st.configure("TCombobox", fieldbackground=RAISED, background=RAISED, foreground=TEXT, arrowcolor=DIM,
                 bordercolor=LINE, lightcolor=RAISED, darkcolor=RAISED, padding=(8, 4))
    st.map("TCombobox", fieldbackground=[("readonly", RAISED)], foreground=[("readonly", TEXT)],
           selectbackground=[("readonly", RAISED)], selectforeground=[("readonly", TEXT)])
    top.option_add("*TCombobox*Listbox.background", RAISED)
    top.option_add("*TCombobox*Listbox.foreground", TEXT)
    top.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    top.option_add("*TCombobox*Listbox.font", (FONT, 11))
    W = 480                       # ширина содержимого
    pending = {}                  # что сохранено, но заработает после перезапуска
    refreshers = []               # обновить вид плиток/переключателей (раз в 0,7 с и после клика)

    def save(changes, restart_what=None):
        s.update(changes)
        try:
            save_settings(changes)
        except OSError as e:
            log.exception("Пульт: не смог записать settings.json")
            message(T(f"Не смог записать settings.json: {e}", f"Could not write settings.json: {e}"))
            return
        if restart_what:
            pending[restart_what] = True
            show_restart()
        if win is not None:
            win.last = None       # плашка перерисуется с новыми настройками

    def lab(parent, text="", size=11, col=TEXT, semi=False, bg=None, **kw):
        return tk.Label(parent, text=text, bg=bg or parent["bg"], fg=col, font=(SEMI if semi else FONT, size), **kw)

    def clickable(w, cmd, hover=None):
        w.configure(cursor="hand2")
        w.bind("<Button-1>", lambda e: cmd())
        if hover:
            normal = w["fg"]
            w.bind("<Enter>", lambda e: w.configure(fg=hover))
            w.bind("<Leave>", lambda e: w.configure(fg=normal))

    def box(parent, bg=PANEL, **kw):
        return tk.Frame(parent, bg=bg, highlightbackground=LINE, highlightcolor=LINE, highlightthickness=1, **kw)

    import base64
    pics = {}       # готовые сглаженные картинки (tkinter сам рисует круги «лесенкой» — Макс заметил)

    def bgr(colour):
        h = colour.lstrip("#")
        return int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)

    def smooth(key, w, h, bg, paint):
        """Картинка w×h: рисуем в 4 раза крупнее (OpenCV, сглаживание) и уменьшаем — края круглые и гладкие.
        Pillow в сборку программы не входит, поэтому всё через OpenCV; окну отдаём PNG (Tk читает сам)."""
        if (key, bg) not in pics:
            k = 4
            im = np.zeros((h * k, w * k, 3), np.uint8)
            im[:] = bgr(bg)
            paint(im, k)
            ok, png = cv2.imencode(".png", cv2.resize(im, (w, h), interpolation=cv2.INTER_AREA))
            pics[(key, bg)] = tk.PhotoImage(master=top, data=base64.b64encode(png.tobytes()).decode("ascii"))
        return pics[(key, bg)]

    def capsule(im, x0, y0, x1, y1, colour):
        r = (y1 - y0) // 2
        cv2.circle(im, (x0 + r, y0 + r), r, bgr(colour), -1, cv2.LINE_AA)
        cv2.circle(im, (x1 - r, y0 + r), r, bgr(colour), -1, cv2.LINE_AA)
        cv2.rectangle(im, (x0 + r, y0), (x1 - r, y0 + 2 * r), bgr(colour), -1)

    def pill(on):
        def paint(im, k):
            fill, edge = (ACCENT, ACCENT) if on else (OFF, LINE)
            capsule(im, 0, 0, 42 * k - 1, 24 * k - 1, edge)            # рамка
            capsule(im, k, k, 42 * k - 1 - k, 24 * k - 1 - k, fill)    # заливка
            cx = (29 if on else 13) * k
            cv2.circle(im, (cx, 12 * k), 7 * k + k // 2, bgr(BG if on else FAINT), -1, cv2.LINE_AA)
        return paint

    LINK_SHAPES = {   # фигуры на оранжевом кружке 15×15 (в точках кружка)
        "✈": [[(3.0, 7.4), (12.0, 3.4), (9.9, 11.8), (7.2, 9.4)], [(7.2, 9.4), (12.0, 3.4), (6.6, 11.6)]],
        "♥": [[(3.3, 7.0), (11.7, 7.0), (7.5, 11.8)]],
    }

    def link_icon(letter):
        def paint(im, k):
            disc(ACCENT, 15)(im, k)
            ink = bgr(BG)
            for poly in LINK_SHAPES[letter]:
                pts = np.array([[round(x * k), round(y * k)] for x, y in poly], np.int32)
                cv2.fillPoly(im, [pts], ink, cv2.LINE_AA)
            if letter == "♥":
                for cx in (5.5, 9.5):
                    cv2.circle(im, (round(cx * k), round(6.1 * k)), round(2.45 * k), ink, -1, cv2.LINE_AA)
        return paint

    def disc(colour, size):
        return lambda im, k: cv2.circle(im, (size * k // 2, size * k // 2), size * k // 2 - k // 2, bgr(colour), -1,
                                        cv2.LINE_AA)

    def toggle(parent, get, on_click):
        """Переключатель-«таблетка»: включено — оранжевый, кружок справа; выключено — тёмный, кружок слева."""
        c = tk.Canvas(parent, width=42, height=24, bg=parent["bg"], highlightthickness=0, cursor="hand2")

        def draw():
            c.delete("all")
            on = bool(get())
            c.create_image(0, 0, image=smooth(("pill", on), 42, 24, parent["bg"], pill(on)), anchor="nw")

        c.bind("<Button-1>", lambda e: (on_click(), draw()))
        refreshers.append(draw)
        draw()
        return c

    def card(title, hint=None):
        f = box(body)
        f.pack(fill="x", pady=(0, 8))
        inner = tk.Frame(f, bg=PANEL, padx=16, pady=7)
        inner.pack(fill="x")
        h = tk.Frame(inner, bg=PANEL)
        h.pack(fill="x", pady=(0, 2))
        lab(h, title, 9, ACCENT, semi=True).pack(side="left")
        if hint:
            lab(h, hint, 9, FAINT).pack(side="right")
        inner.first = True
        return inner

    def row(inner, title, small=None):
        if not inner.first:
            tk.Frame(inner, bg=ROWLINE, height=1).pack(fill="x")
        inner.first = False
        r = tk.Frame(inner, bg=PANEL, pady=4)
        r.pack(fill="x")
        left = tk.Frame(r, bg=PANEL)
        left.pack(side="left")
        lab(left, title, 11).pack(anchor="w")
        if small:
            lab(left, small, 8, FAINT).pack(anchor="w")
        return r

    def open_url(url, what):
        log.info("Пульт: открываю %s (%s)", what, url)
        try:
            os.startfile(url)
        except OSError as e:
            log.exception("Пульт: не открылось %s", url)
            message(T(f"Не открылось: {e}", f"Could not open: {e}"))

    outer = tk.Frame(top, bg=BG)
    outer.pack(fill="both")
    tk.Frame(outer, bg=ACCENT, width=4).pack(side="left", fill="y")     # оранжевая полоса слева, как у плашки
    # содержимое — на холсте с прокруткой: на маленьком экране (или при масштабе 125–150 %) окно не выше экрана
    holder = tk.Canvas(outer, bg=BG, highlightthickness=0, yscrollincrement=20)
    holder.pack(side="left", fill="both", expand=True)
    body = tk.Frame(holder, bg=BG, padx=20, pady=12, width=W)
    holder.create_window(0, 0, window=body, anchor="nw")

    def fit(_=None):
        w, h = body.winfo_reqwidth(), body.winfo_reqheight()
        holder.configure(width=w, height=min(h, top.winfo_screenheight() - 90), scrollregion=(0, 0, w, h))

    body.bind("<Configure>", fit)
    top.bind("<MouseWheel>", lambda e: holder.yview_scroll(-3 if e.delta > 0 else 3, "units")
             if body.winfo_reqheight() > holder.winfo_height() else None)

    # ---- шапка: значок, название, RU / EN
    head = tk.Frame(body, bg=BG)
    head.pack(fill="x", pady=(0, 10))
    logo = tk.Canvas(head, width=46, height=46, bg=BG, highlightthickness=0)
    logo.create_rectangle(0, 0, 45, 45, fill=RAISED, outline=LINE)
    logo.create_text(23, 23, text="W", fill=ACCENT, font=(SEMI, 20))
    logo.pack(side="left")
    names = tk.Frame(head, bg=BG, padx=12)
    names.pack(side="left")
    line1 = tk.Frame(names, bg=BG)
    line1.pack(anchor="w")
    lab(line1, "Wardogs Overlay", 17, semi=True).pack(side="left")
    lab(line1, VERSION, 9, DIM).pack(side="left", padx=(6, 0), pady=(6, 0))
    lab(names, T("Бафы и дебафы обвесов, TTK и оружейник", "Attachment buffs and debuffs, TTK and gunsmith"),
        9, DIM).pack(anchor="w")
    seg = box(head, bg=BG)
    seg.pack(side="right")

    def set_lang(code):
        if code == LANG["code"]:
            return
        log.info("Пульт: язык → %s, перезапускаюсь", code)
        save({"language": code})
        ev(T("Язык изменён — перезапускаюсь", "Language changed — restarting"), "ok")
        top.destroy()
        on_restart()

    for code in ("ru", "en"):
        on = code == LANG["code"]
        b = lab(seg, code.upper(), 9, BG if on else DIM, semi=True, bg=ACCENT if on else BG, padx=11, pady=5)
        b.pack(side="left")
        if not on:
            clickable(b, lambda c=code: set_lang(c), hover=TEXT)

    # ---- плитки состояния
    tiles = tk.Frame(body, bg=BG)
    tiles.pack(fill="x", pady=(0, 8))
    tiles.columnconfigure(0, weight=1, uniform="t")
    tiles.columnconfigure(1, weight=1, uniform="t")

    def tile(r, c, title):
        f = box(tiles)
        f.grid(row=r, column=c, sticky="nsew", padx=(0, 5) if c == 0 else (5, 0), pady=(0, 8) if r == 0 else 0)
        inner = tk.Frame(f, bg=PANEL, padx=14, pady=8)
        inner.pack(fill="both", expand=True)
        top_row = tk.Frame(inner, bg=PANEL)
        top_row.pack(fill="x")
        lab(top_row, title.upper(), 8, DIM).pack(side="left")
        val_row = tk.Frame(inner, bg=PANEL)
        val_row.pack(fill="x", pady=(4, 0))
        v, sub = tk.StringVar(), tk.StringVar()
        val = lab(val_row, "", 14, semi=True, textvariable=v, anchor="w")
        val.pack(side="left")
        sub_row = tk.Frame(inner, bg=PANEL)
        sub_row.pack(fill="x", pady=(2, 0))
        lab(sub_row, "", 8, FAINT, textvariable=sub, anchor="w", justify="left").pack(side="left")
        tile.sub_row = sub_row
        return top_row, val_row, v, sub

    scan_top, _, scan_v, scan_sub = tile(0, 0, T("Поиск меню", "Menu search"))
    toggle(scan_top, lambda: SCAN["active"].is_set(), lambda: toggle_scan("Пульт")).pack(side="right")
    _, game_row, game_v, game_sub = tile(0, 1, T("Игра", "Game"))
    dot = tk.Canvas(game_row, width=12, height=12, bg=PANEL, highlightthickness=0)
    dot.pack(side="left", before=game_row.winfo_children()[0], padx=(0, 6))
    _, _, plate_v, plate_sub = tile(1, 0, T("Плашка", "Panel"))
    _, _, db_v, db_sub = tile(1, 1, T("База MetaForge", "MetaForge data"))
    db_link = lab(tile.sub_row, T("· обновить", "· update"), 8, ACCENT)
    db_link.pack(side="left", padx=(4, 0))

    def db_update():
        if ctx.get("updating") or ctx.get("rec") is None:
            return

        def run():
            ctx["updating"] = True
            try:
                ok, text = update_db_now(ctx["rec"])
                log.info("Пульт: обновление базы — %s", text)
            finally:
                ctx["updating"] = False
        threading.Thread(target=run, daemon=True, name="обновление-базы").start()

    def refresh_tiles():
        hk = s.get("hotkey", "")
        if ctx.get("rec") is None:
            scan_v.set(T("Загружается…", "Loading…"))
            scan_sub.set("")
        elif SCAN["active"].is_set():
            scan_v.set(T("Идёт", "Running"))
            off = int(float(s.get("auto_off_seconds", 10)))
            scan_sub.set(T(f"засыпает через {off} с без меню", f"pauses after {off} s without the menu") if off
                         else T("не засыпает", "never pauses"))
        else:
            scan_v.set(T("Спит", "Paused"))
            scan_sub.set(T(f"включить — {hk} или переключатель", f"turn on — {hk} or the switch"))
        g = STATUS["game"]
        game_v.set({None: T("Ещё не искал", "Not checked yet"), True: T("Найдена", "Found"),
                    False: T("Не найдена", "Not found")}[g])
        game_sub.set(T("меню «Модули» открыто", "«ATTC.» menu is open") if STATUS["menu"] else
                     T("Продавец → Экипировка → Модули", "Vendor → Equipment → ATTC.") if g else
                     T("запусти Wardogs (окно без рамки)", "start Wardogs (borderless window)"))
        dot.delete("all")
        dot.create_image(0, 0, image=smooth(("dot", bool(g)), 12, 12, PANEL, disc(GOOD if g else FAINT, 12)), anchor="nw")
        shown = STATUS["shown"] or {}
        if shown.get("weapon"):
            tab = T("Боеприпасы", "Ammo") if shown.get("tab") == "ammo" else T("Модули", "ATTC.")
            plate_v.set(f"{shown['weapon']} · {tab}")
        else:
            plate_v.set(T("Не показана", "Hidden"))
        plate_sub.set(T("видно только в OBS", "visible only in OBS") if not s.get("show_window", True) else
                      T("встаёт рядом с меню игры", "appears next to the game menu"))
        date = str(BALL.get("generatedAt") or "")[:10]
        db_link.configure(text="" if ctx.get("updating") or ctx.get("rec") is None else T("· обновить", "· update"))
        db_v.set(T("обновляю…", "updating…") if ctx.get("updating") else
                 (T("от ", "from ") + ".".join(reversed(date.split("-")[1:]))) if date else "—")
        db_sub.set(T(f"{len(WEAPONS)} пушек · {len(ATTS)} обвесов", f"{len(WEAPONS)} weapons · {len(ATTS)} attachments"))

    refreshers.append(refresh_tiles)
    clickable(db_link, db_update, hover=TEXT)

    # ---- кнопки
    btns = tk.Frame(body, bg=BG)
    btns.pack(fill="x", pady=(0, 8))
    for i in range(3):
        btns.columnconfigure(i, weight=1, uniform="b")

    def events_file():
        if not os.path.exists(EVENTS_FILE):
            open(EVENTS_FILE, "a", encoding="utf-8").close()
        open_url(EVENTS_FILE, "журнал событий")

    for i, (text, cmd, main) in enumerate(((T("Инструкция", "Manual"), open_manual, True),
                                           (T("Журнал событий", "Event log"), events_file, False),
                                           (T("Папка программы", "Program folder"), lambda: open_url(HERE, "папку"), False))):
        b = tk.Label(btns, text=text, bg=ACCENT if main else RAISED, fg=BG if main else TEXT, pady=6,
                     font=(SEMI if main else FONT, 11), highlightbackground=ACCENT if main else LINE, highlightthickness=1)
        b.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 4, 0 if i == 2 else 4))
        clickable(b, cmd, hover=None if main else ACCENT)

    # ---- карточка «Плашка»
    c1 = card(T("ПЛАШКА", "PANEL"))
    r = row(c1, T("Непрозрачность", "Opacity"))
    op_text = tk.StringVar()
    lab(r, "", 10, DIM, textvariable=op_text, width=4, anchor="e").pack(side="right")
    sl = tk.Canvas(r, width=160, height=20, bg=PANEL, highlightthickness=0, cursor="hand2")
    sl.pack(side="right", padx=(0, 8))
    LO, HI = 0.4, 1.0

    def draw_slider():
        v = float(s.get("opacity", 0.92))
        x = 6 + (v - LO) / (HI - LO) * 148
        sl.delete("all")
        sl.create_rectangle(6, 8, 154, 13, fill=OFF, width=0)
        sl.create_rectangle(6, 8, x, 13, fill=ACCENT, width=0)
        sl.create_rectangle(x - 5, 2, x + 5, 18, fill=TEXT, width=0)
        op_text.set(f"{v * 100:.0f}%")

    def slide(e, final=False):
        v = round(min(HI, max(LO, LO + (e.x - 6) / 148 * (HI - LO))), 2)
        s["opacity"] = v
        if win is not None:
            win.root.attributes("-alpha", v)
        draw_slider()
        if final:
            save({"opacity": v})

    sl.bind("<Button-1>", slide)
    sl.bind("<B1-Motion>", slide)
    sl.bind("<ButtonRelease-1>", lambda e: slide(e, True))
    draw_slider()

    def flip(key, restart_what=None, default=False):
        return lambda: save({key: not s.get(key, default)}, restart_what)

    for key, title, small, rw, default in (
            ("gunsmith", T("Оружейник рекомендует", "Gunsmith suggests"), T("советует дуло и рукоять", "suggests muzzle and grip"), None, False),
            ("detail", T("Подробно", "Details"), T("все статы, а не 4 главных", "every stat, not just the main 4"), None, False),
            ("show_window", T("Показывать плашку у меня на экране", "Show the panel on my screen"),
             T("выкл — только в OBS", "off — only in OBS"), T("плашка на экране", "panel on screen"), True),
            ("obs_server", T("Плашка для OBS", "Panel for OBS"), T("Источник «Браузер» · 1920×1080", "Source «Browser» · 1920×1080"),
             "OBS", False)):
        r = row(c1, title, small)
        toggle(r, lambda k=key, d=default: s.get(k, d), flip(key, rw, default)).pack(side="right")
    obs_url = f"http://127.0.0.1:{server.PORT}/overlay.html"
    obs_box = tk.Frame(c1, bg=PANEL)
    url_var = top.url_var = tk.StringVar(value=obs_url)   # держим ссылку — иначе поле окажется пустым
    tk.Entry(obs_box, textvariable=url_var, state="readonly", readonlybackground=RAISED, fg=TEXT, relief="flat",
             font=(FONT, 10), width=34, highlightthickness=1, highlightbackground=LINE).pack(side="left", ipady=3)
    copied = tk.StringVar()

    def copy_url():
        top.clipboard_clear()
        top.clipboard_append(obs_url)
        copied.set(T("скопировано", "copied"))
        log.info("Пульт: адрес для OBS скопирован")
        top.after(2500, lambda: copied.set(""))

    cb = tk.Label(obs_box, text=T("Копировать", "Copy"), bg=RAISED, fg=TEXT, font=(FONT, 10), padx=10, pady=3,
                  highlightbackground=LINE, highlightthickness=1)
    cb.pack(side="left", padx=8)
    clickable(cb, copy_url, hover=ACCENT)
    lab(obs_box, "", 9, GOOD, textvariable=copied).pack(side="left")

    def obs_visible():
        if s.get("obs_server"):
            if not obs_box.winfo_ismapped():
                obs_box.pack(fill="x", pady=(0, 8))
        elif obs_box.winfo_ismapped():
            obs_box.pack_forget()

    refreshers.append(obs_visible)

    # ---- карточка «Клавиши в игре»: каждая комбинация — своя плитка, клавиши нарисованы как на клавиатуре
    c2 = card(T("КЛАВИШИ В ИГРЕ", "IN-GAME KEYS"), T("нажми на плитку, чтобы поменять", "click a tile to change"))
    combos = tk.Frame(c2, bg=PANEL)
    combos.pack(fill="x", pady=(4, 2))
    combos.columnconfigure(0, weight=1, uniform="k")
    combos.columnconfigure(1, weight=1, uniform="k")
    waiting = {"key": None, "hint": ""}

    def keycaps(cv, text):
        cv.delete("all")
        parts = [p.strip() for p in text.split("+") if p.strip()] if text else []
        # одна клавиша поиска (F8, Insert…) занята программой во всей Windows — в игре она работать не будет
        solo = cv.key == "hotkey" and len(parts) == 1 and not (waiting["key"] == cv.key)
        cv.configure(height=52 if solo else 36)
        if solo:
            cv.create_text(2, 45, text=T("в игре эта клавиша работать не будет", "this key won't work in the game"),
                           fill=FAINT, font=(FONT, 8), anchor="w")
        if waiting["key"] and cv.key == waiting["key"]:
            cv.create_text(2, 10, text=waiting["hint"] or T("нажми клавишу или сочетание", "press a key or a combination"),
                           fill=ACCENT, font=(FONT, 10), anchor="w")
            cv.create_text(2, 28, text=T("Esc — отмена", "Esc — cancel"), fill=FAINT, font=(FONT, 8), anchor="w")
            return
        x = 2
        for i, p in enumerate(parts):
            main = i == len(parts) - 1
            t = cv.create_text(0, -50, text=p, font=(SEMI, 10))
            w = max(34, cv.bbox(t)[2] - cv.bbox(t)[0] + 18)
            cv.delete(t)
            cv.create_rectangle(x, 4, x + w, 34, fill="#8a6420" if main else KEY, outline="")      # «низ» клавиши
            cv.create_rectangle(x, 2, x + w, 30, fill=ACCENT if main else "#262b1e",
                                outline=ACCENT if main else KEY)
            cv.create_text(x + w / 2, 16, text=p, fill=BG if main else TEXT, font=(SEMI, 10))
            x += w
            if not main:
                cv.create_text(x + 8, 17, text="+", fill=FAINT, font=(SEMI, 10))
                x += 16

    caps = {}
    for i, (key, title, small) in enumerate((("hotkey", T("Поиск меню", "Menu search"), T("вкл / выкл", "on / off")),
                                             ("compare_hotkey", T("Сравнить сборку", "Compare builds"),
                                              T("запомнить / сброс", "save / reset")))):
        f = tk.Frame(combos, bg=BG, highlightbackground=LINE, highlightthickness=1, padx=12, pady=6, cursor="hand2")
        f.grid(row=0, column=i, sticky="nsew", padx=(0, 5) if i == 0 else (5, 0))
        hrow = tk.Frame(f, bg=BG)
        hrow.pack(fill="x", pady=(0, 4))
        lab(hrow, title, 10, semi=True).pack(side="left")
        lab(hrow, small, 8, FAINT).pack(side="right", pady=(2, 0))
        cv = tk.Canvas(f, width=200, height=36, bg=BG, highlightthickness=0)
        cv.key = key
        cv.pack(anchor="w")
        caps[key] = cv
        keycaps(cv, s.get(key, ""))

        def start(k=key):
            waiting["key"], waiting["hint"] = k, ""
            log.info("Пульт: жду новое сочетание для %s", k)
            for kk, c in caps.items():
                keycaps(c, s.get(kk, ""))
            top.focus_force()

        for w in (f, hrow, cv, *hrow.winfo_children()):
            w.bind("<Button-1>", lambda e, k=key: start(k))

    def key_name(vk):
        """Код клавиши Windows → как её пишем: буква, цифра, F1–F24, Insert, Num5… (от раскладки не зависит)."""
        if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
            return chr(vk)
        if 0x70 <= vk <= 0x87:
            return f"F{vk - 0x6F}"
        return next((SPECIAL_NAMES[k] for k, v in SPECIAL_KEYS.items() if v == vk), None)

    def held_mods():
        """Какие Ctrl / Shift / Alt / Win зажаты сейчас — спрашиваем Windows (надёжнее, чем Tk)."""
        ks = ctypes.windll.user32.GetKeyState
        return [n for codes, n in (((0x11,), "Ctrl"), ((0x10,), "Shift"), ((0x12,), "Alt"), ((0x5B, 0x5C), "Win"))
                if any(ks(c) & 0x8000 for c in codes)]

    def redraw_caps():
        for kk, c in caps.items():
            keycaps(c, s.get(kk, ""))

    def cancel_wait():
        log.info("Пульт: смену клавиши отменили")
        waiting["key"], waiting["hint"] = None, ""
        redraw_caps()

    def on_key(e):
        k = waiting["key"]
        if not k:
            return
        vk = e.keycode
        if vk == 27:                                   # Esc
            return cancel_wait()
        mods = held_mods()
        if vk in (0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5):
            waiting["hint"] = " + ".join(mods) + " + …"      # зажал Ctrl/Shift — ждём основную клавишу
            return redraw_caps()
        name = key_name(vk)
        if not name:
            waiting["hint"] = T("эта не подходит: буква, цифра, F1–F12, Insert…", "not this one: letter, digit, F1–F12, Insert…")
            log.info("Пульт: клавиша с кодом %d не подходит", vk)
            return redraw_caps()
        if not [m for m in mods if m != "Win"] and not solo_ok(vk):
            waiting["hint"] = T(f"«{name}» одну нельзя — с Ctrl, Shift или Alt", f"«{name}» alone — add Ctrl, Shift or Alt")
            return redraw_caps()
        text = "+".join(mods + [name])
        try:
            parse_hotkey(text)
        except ValueError as err:
            waiting["hint"] = str(err)
            log.info("Пульт: сочетание %s не подходит: %s", text, err)
            return redraw_caps()
        waiting["key"], waiting["hint"] = None, ""
        log.info("Пульт: новое сочетание для %s — %s", k, text)
        if text != s.get(k):
            save({k: text}, T("клавиши", "keys"))
        redraw_caps()

    top.bind("<KeyPress>", on_key)

    # ---- карточка «Разное»
    c3 = card(T("РАЗНОЕ", "OTHER"))
    mons = [T("сам найдёт", "auto")] + [T(f"монитор {i}", f"monitor {i}") for i in range(1, 5)]
    mon = s.get("monitor")
    v_mon = tk.StringVar(value=mons[mon] if isinstance(mon, int) and 1 <= mon <= 4 else mons[0])
    r = row(c3, T("Где игра", "Game monitor"))
    cm = ttk.Combobox(r, textvariable=v_mon, values=mons, state="readonly", width=14, font=(FONT, 10))
    cm.pack(side="right")
    cm.bind("<<ComboboxSelected>>", lambda e: save({"monitor": "auto" if v_mon.get() == mons[0] else mons.index(v_mon.get())},
                                                   T("монитор", "monitor")))
    offs = [0, 5, 10, 20, 30, 60, 120, 300]
    off_names = [T("никогда", "never") if o == 0 else T(f"{o} секунд", f"{o} seconds") for o in offs]
    cur = int(float(s.get("auto_off_seconds", 10)))
    if cur not in offs:
        offs.append(cur)
        off_names.append(T(f"{cur} секунд", f"{cur} seconds"))
    v_off = tk.StringVar(value=off_names[offs.index(cur)])
    r = row(c3, T("Засыпать без меню через", "Pause search without the menu after"))
    co = ttk.Combobox(r, textvariable=v_off, values=off_names, state="readonly", width=14, font=(FONT, 10))
    co.pack(side="right")
    co.bind("<<ComboboxSelected>>", lambda e: save({"auto_off_seconds": offs[off_names.index(v_off.get())]}))
    r = row(c3, T("Сообщать о новой версии", "Tell me about new versions"),
            T("смотрит руководство Steam, ничего не скачивает", "checks the Steam guide, downloads nothing"))
    toggle(r, lambda: s.get("check_updates", True), flip("check_updates", None, True)).pack(side="right")

    # ---- полоса «нужен перезапуск» (появляется, когда поменяли то, что работает только после перезапуска)
    rbar = tk.Frame(body, bg=BG, highlightbackground=ACCENT, highlightthickness=1, padx=12, pady=8)
    r_text = tk.StringVar()
    lab(rbar, "", 10, TEXT, textvariable=r_text, bg=BG, anchor="w", justify="left", wraplength=300).pack(side="left")

    def do_restart():
        ev(T("Настройки изменены — перезапускаюсь", "Settings changed — restarting"), "ok")
        top.destroy()
        on_restart()

    rb = tk.Label(rbar, text=T("Перезапустить", "Restart"), bg=ACCENT, fg=BG, font=(SEMI, 10), padx=12, pady=4)
    rb.pack(side="right")
    clickable(rb, do_restart)

    def show_restart():
        r_text.set(T("Сохранено. Заработает после перезапуска: ", "Saved. Works after a restart: ") + ", ".join(pending))
        if not rbar.winfo_ismapped():
            rbar.pack(fill="x", pady=(0, 8), before=foot)
            top.after(50, lambda: holder.yview_moveto(1.0))   # полосу видно, даже если окно с прокруткой

    # ---- подвал: данные и ссылки
    foot = tk.Frame(body, bg=BG)
    foot.pack(fill="x", pady=(2, 0))
    links = tk.Frame(foot, bg=BG)          # ссылки — строкой, «Данные: …» — под ними (в одну строку не влезает)
    links.pack(fill="x")
    lab(foot, T("Данные: metaforge.app", "Data: metaforge.app"), 8, FAINT).pack(anchor="w", pady=(4, 0))
    for text, letter, url in ((T("Руководство", "Guide"), "S", guide_url()), ("Twitch", "T", TWITCH_URL),
                              ("Telegram", "✈", TELEGRAM_URL), (T("Ваша поддержка", "Support us"), "♥", DONATE_URL)):
        l = tk.Frame(links, bg=BG, cursor="hand2")
        l.pack(side="right" if url == DONATE_URL else "left", padx=(0, 14))
        ic = tk.Canvas(l, width=15, height=15, bg=BG, highlightthickness=0)
        if letter in LINK_SHAPES:      # самолётик и сердце — рисуем сами (символов ✈ ♥ в Bahnschrift нет — «каша»)
            ic.create_image(0, 0, image=smooth(("link", letter), 15, 15, BG, link_icon(letter)), anchor="nw")
        else:
            ic.create_image(0, 0, image=smooth("link", 15, 15, BG, disc(ACCENT, 15)), anchor="nw")
            ic.create_text(7, 7, text=letter, fill=BG, font=(SEMI, 7))
        ic.pack(side="left", padx=(0, 4))
        t = lab(l, text, 9, ACCENT)
        t.pack(side="left")
        for w in (l, ic, t):
            w.bind("<Button-1>", lambda e, u=url, n=text: open_url(u, n))

    def tick():
        if not top.winfo_exists():
            return
        for f in refreshers:
            try:
                f()
            except Exception:
                log.exception("Пульт: ошибка при обновлении")
        top.after(700, tick)

    tick()
    top.bind("<Escape>", lambda e: cancel_wait() if waiting["key"] else top.destroy())   # Esc: отмена ввода клавиш, иначе закрыть
    top.update_idletasks()
    sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
    top.geometry(f"+{(sw - top.winfo_width()) // 2}+{max(0, (sh - top.winfo_height()) // 3)}")
    top.lift()
    top.focus_force()
    # «поверх всех» — только чтобы появиться над игрой; дальше обычное окно (Alt+Tab, другие программы — поверх)
    top.after(600, lambda: top.winfo_exists() and top.attributes("-topmost", False))
    log.info("Открыл пульт (окно настроек)")
    return top


def main():
    testing = len(sys.argv) > 2 and sys.argv[1] == "--test"
    # в меню журнал на экран не выводим — там своё окно со статусом и лентой событий
    setup_log("overlay_test.log" if testing else "overlay.log", console_log="--menu" not in sys.argv)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # настоящие пиксели при масштабе Windows 125%/150%
    except Exception as e:
        log.warning("Не удалось включить точные пиксели (DPI): %s", e)
    settings = load_settings()
    LANG["code"] = pick_language(settings.get("language"))
    apply_language()
    log.setLevel(str(settings.get("log_level", "DEBUG")).upper())
    log.info("===== Запуск: %s =====", " ".join(sys.argv[1:]) or "обычный режим")
    log.info("Python %s, Windows %s, папка %s", platform.python_version(), platform.version(), HERE)
    log.info("Настройки: %s", json.dumps(settings, ensure_ascii=False))
    log.info("База: %d пушек, %d обвесов, %d иконок", len(WEAPONS), len(ATTS), len(set(ICONS.values())))

    if testing:
        t0 = time.time()
        rec = Recognizer(settings["match_threshold"], use_disk_cache=False)  # настоящую память не трогаем
        log.info("Распознавание текста загружено за %.1f с", time.time() - t0)
        loc = Locator()
        for p in sys.argv[2:]:
            img = read_rgb(p)
            for attempt in ("первый раз", "повторно"):
                log.info("--- %s (%dx%d), %s", os.path.basename(p), img.shape[1], img.shape[0], attempt)
                t = time.time()
                hit = loc.search(_gray(img), default_scales(*img.shape[:2]), img)
                t_find = time.time() - t
                res = (rec.analyze(to_reference(img, *hit), native=(img,) + hit, mode=loc.mode) if hit
                       else "меню не найдено")
                where = (f"вкладка «{TAB_RU[loc.mode]}», масштаб {hit[0]:.2f}, надпись в ({hit[1]:.0f},{hit[2]:.0f})"
                         if hit else "")
                line = (f"{os.path.basename(p)} {img.shape[1]}x{img.shape[0]} {attempt}: "
                        f"поиск {t_find:.2f}s, всего {time.time() - t:.2f}s; {where}; {res}")
                log.info("Итог: %s", line)
                print(line)
        return

    menu = "--menu" in sys.argv          # консольная версия с меню («Wardogs Overlay.bat»)
    tray_mode = "--tray" in sys.argv     # значок у часов (WardogsOverlay.exe)
    ok = take_single_instance()
    if not ok and "--restarted" in sys.argv:   # перезапуск из меню: ждём, пока старая копия закроется
        for _ in range(30):
            time.sleep(0.5)
            ok = take_single_instance()
            if ok:
                break
    if not ok:
        log.warning("Уже запущена другая копия — эта закрывается")
        message(T("Оверлей Wardogs уже запущен.\n\nЕго меню — значок у часов (правой кнопкой). "
                  "Чтобы перезапустить — сначала выйди из него.",
                  "Wardogs overlay is already running.\n\nIts menu is the tray icon next to the clock (right click). "
                  "To restart it, exit it first."))
        return
    listen_for_quit()
    events_start()
    ev(T(f"Версия {VERSION}", f"Version {VERSION}"), "dim")
    SCAN["hotkey"] = str(settings.get("hotkey", "Ctrl+Shift+O"))
    listen_hotkey(SCAN["hotkey"])
    listen_compare_key(str(settings.get("compare_hotkey", "Ctrl+Shift+S")))
    if menu:
        ctypes.windll.kernel32.SetConsoleTitleW("Wardogs оверлей")
        print("\n Загружаю распознавание… (несколько секунд)", flush=True)
    tray = None
    ctx = {"rec": None, "new": None, "updating": False}
    if tray_mode:   # значок появляется сразу, пока грузится распознавание (несколько секунд)
        tray = start_tray(settings, ctx)

    log.info("Загружаю распознавание…")
    t0 = time.time()
    rec = Recognizer(settings["match_threshold"])
    ctx["rec"] = rec
    log.info("Распознавание текста загружено за %.1f с", time.time() - t0)
    SCAN["active"].set()   # при запуске сразу ищем; нет меню — уснём через auto_off_seconds
    if tray:
        tray.set_tip(T(f"Wardogs оверлей {VERSION}", f"Wardogs overlay {VERSION}"))
    if menu:
        on_console_close(rec)
        threading.Thread(target=console_menu, args=(settings, rec), daemon=True, name="меню").start()
    if settings.get("check_updates", True):
        def on_new(v):
            ctx["new"] = v
            if tray:
                tray.notify(T(f"Вышла версия {v}", f"Version {v} is out"),
                            T("Скачать — пункт «Скачать новую версию» в меню значка", "Download — «Download new version» in the tray menu"))
            else:
                UI_MESSAGES.append((T(f"ВЫШЛА ВЕРСИЯ {v}", f"VERSION {v} IS OUT"),
                                    T("Скачать — по ссылке в руководстве Steam", "Download — link in the Steam guide"), 10))
        check_update(on_new)
    if settings.get("obs_server"):
        server.VIEW["make"] = lambda st: plate_model(st, settings)
        try:
            server.start(background=True)
        except OSError as e:
            log.error("Сервер для OBS не запустился (адрес 127.0.0.1:%d занят?): %s", server.PORT, e)
    else:
        log.info("Сервер для OBS выключен (включить — \"obs_server\": true в settings.json)")
        if not settings["show_window"]:
            log.warning("И окно поверх игры, и сервер для OBS выключены — плашку не будет видно нигде")
    stop = threading.Event()
    fake = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--fake" else None  # проверка без игры
    threading.Thread(target=capture_loop, args=(settings, rec, stop, fake), daemon=True, name="распознавание").start()
    log.info("Работаю. Открой в игре: Продавец → Экипировка → Модули. Выход — %s.",
             "меню значка у часов" if tray_mode else "пункт 0 в меню")

    show = settings["show_window"]
    try:
        if not show and not tray_mode:
            while not server.quit_event.wait(1):
                pass
        else:   # окно нужно и без плашки: в нём живёт окно настроек
            win = OverlayWindow(settings)
            if show:
                win.flash(T("ОВЕРЛЕЙ WARDOGS ЗАПУЩЕН", "WARDOGS OVERLAY STARTED"),
                          T(f"Искать меню — {settings.get('hotkey')} · ", f"Menu search — {settings.get('hotkey')} · ")
                          + (T("меню — значок у часов (правой кнопкой)", "menu — tray icon next to the clock (right click)")
                             if tray_mode else T("закрыть — пункт 0 в окне программы", "close — item 0 in the program window")))
            elif tray:
                tray.notify(T("Оверлей Wardogs запущен", "Wardogs overlay started"),
                            T("Плашка выключена в настройках. Меню — правой кнопкой по значку",
                              "The panel is off in settings. Menu — right click the icon"))
            opened = {"settings": None}

            def restart():
                RESTART["now"] = True
                RESTART["settings"] = True    # перезапуск из пульта — новая копия сразу снова откроет пульт (Макс)
                server.quit_event.set()

            if "--settings" in sys.argv:
                log.info("Перезапустился из пульта — открываю пульт снова")
                UI_ACTIONS.append("settings")

            def tick():
                if server.quit_event.is_set():
                    log.info("Получил команду выключиться")
                    win.root.destroy()
                    return
                while UI_MESSAGES:   # «поиск включён/выключен» от горячей клавиши
                    title, sub, secs = UI_MESSAGES.pop(0)
                    if show:
                        win.flash(title, sub, secs)
                while UI_ACTIONS:    # пункты меню значка, которым нужно окно (tkinter — только здесь)
                    if UI_ACTIONS.pop(0) == "settings":
                        top = opened["settings"]
                        if top is not None and top.winfo_exists():
                            top.lift()
                            top.focus_force()
                        else:
                            try:     # ошибка в пульте не должна ронять плашку
                                opened["settings"] = settings_window(win.root, settings, restart, win, ctx)
                            except Exception:
                                log.exception("Пульт не открылся")
                                ev(T("Пульт не открылся — подробности в overlay.log", "Control panel failed — see overlay.log"), "err")
                if show:
                    win.move_to(PLACEMENT["pos"])
                    win.render(server.get_state())
                win.root.after(300, tick)

            tick()
            win.root.mainloop()
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("Окно поверх игры упало")
    stop.set()
    if tray:
        tray.close()
    rec.save(force=True)
    log.info("Память сохранена (%d ячеек). Выключено.", len(rec.mem["slots"]))
    if RESTART["now"]:   # перезапуск (новые настройки): новая копия того же вида
        for h in _handles:
            if isinstance(h, int):
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(h))
        # .exe: новая копия распаковывается в СВОЮ временную папку (иначе берёт папку старой копии,
        # а та, выключаясь, её удаляет — новая падает «No module named onnxruntime…»)
        subprocess.Popen(([sys.executable] if FROZEN else [sys.executable, "-I", os.path.abspath(__file__)])
                         + ["--tray" if tray_mode else "--menu", "--restarted"]
                         + (["--settings"] if RESTART.get("settings") else []), cwd=HERE,
                         env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"))
        log.info("Запустил новую копию")
    time.sleep(0.3)   # дать значку у часов исчезнуть


UI_ACTIONS = []   # из меню значка — в главный поток (окно настроек)
T_SCAN, T_UPDATE_DB, T_SETTINGS, T_EVENTS, T_FOLDER, T_NEW, T_EXIT = 1, 2, 3, 4, 5, 6, 9


def start_tray(settings, ctx):
    """Значок у часов с меню (вместо консольного окна)."""
    import tray as tray_mod
    hk = settings.get("hotkey", "Ctrl+Shift+O")

    def items():
        ready = ctx["rec"] is not None
        out = []
        if ctx["new"]:
            out += [(T(f"★ Скачать новую версию {ctx['new']}…", f"★ Download new version {ctx['new']}…"), T_NEW, False, True), None]
        out += [(T(f"Поиск меню «Модули»\t{hk}", f"Menu search (ATTC. / AMMO)\t{hk}"), T_SCAN, SCAN["active"].is_set(), ready),
                (T("Обновляю базу…", "Updating data…") if ctx["updating"] else T("Обновить базу с MetaForge", "Update data from MetaForge"),
                 T_UPDATE_DB, False, ready and not ctx["updating"]),
                (T("Настройки…", "Settings…"), T_SETTINGS, False, True),
                (T("Журнал событий", "Event log"), T_EVENTS, False, True),
                (T("Открыть папку программы", "Open program folder"), T_FOLDER, False, True),
                None,
                (T(f"Выход   (версия {VERSION})", f"Exit   (version {VERSION})"), T_EXIT, False, True)]
        return out

    def on_command(cid):
        log.info("Меню значка: пункт %d", cid)
        if cid == T_SCAN:
            toggle_scan("Меню значка")
        elif cid == T_UPDATE_DB:
            def run():
                ctx["updating"] = True
                t.notify(T("Обновляю базу…", "Updating data…"), T("Несколько секунд", "A few seconds"))
                try:
                    ok, text = update_db_now(ctx["rec"])
                finally:
                    ctx["updating"] = False
                t.notify(T("База обновлена", "Data updated") if ok else T("База не обновилась", "Data not updated"), text)
            threading.Thread(target=run, daemon=True, name="обновление-базы").start()
        elif cid == T_SETTINGS:
            UI_ACTIONS.append("settings")
        elif cid == T_EVENTS:
            if not os.path.exists(EVENTS_FILE):
                open(EVENTS_FILE, "a", encoding="utf-8").close()
            os.startfile(EVENTS_FILE)
        elif cid == T_FOLDER:
            os.startfile(HERE)
        elif cid == T_NEW:
            os.startfile(guide_url())
        elif cid == T_EXIT:
            log.info("Меню значка: выход")
            server.quit_event.set()

    t = tray_mod.Tray(ICON_FILE if os.path.exists(ICON_FILE) else None,
                      T(f"Wardogs оверлей {VERSION} — загружается…", f"Wardogs overlay {VERSION} — loading…"), items, on_command, double_click=T_SETTINGS)
    return t


# Защита от второго запуска и команда «выключиться» — средствами Windows, без сервера
# (сервер для OBS теперь по желанию). Имена общие для всех версий программы.
SINGLE_INSTANCE_MUTEX = "WardogsOverlaySingleInstance"
QUIT_EVENT_NAME = "WardogsOverlayQuit"
_handles = []   # держим, пока программа работает (иначе Windows их закроет)


def take_single_instance():
    """True — мы единственная копия. False — программа уже запущена."""
    k = ctypes.windll.kernel32
    k.CreateMutexW.restype = ctypes.c_void_p
    h = k.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX)
    already = k.GetLastError() == 183   # ERROR_ALREADY_EXISTS
    if already:
        k.CloseHandle(ctypes.c_void_p(h))   # чужой мьютекс не держим — иначе не дождёмся его освобождения
        return False
    _handles.append(h)
    if server.already_running():   # старая версия (без мьютекса) с включённым сервером
        return False
    return True


def listen_for_quit():
    """Ждём команду «выключиться» снаружи (именованное событие Windows «WardogsOverlayQuit»)."""
    k = ctypes.windll.kernel32
    k.CreateEventW.restype = ctypes.c_void_p
    k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    h = k.CreateEventW(None, True, False, QUIT_EVENT_NAME)
    _handles.append(h)
    # мы единственная копия (проверено раньше) — если команда «выключиться» ещё висит от прошлой копии
    # (её только что закрыли), сбрасываем, иначе новая копия сразу же выключится
    k.ResetEvent.argtypes = [ctypes.c_void_p]
    k.ResetEvent(h)

    def wait():
        k.WaitForSingleObject(h, 0xFFFFFFFF)
        log.info("Получил команду выключиться (снаружи)")
        server.quit_event.set()

    threading.Thread(target=wait, daemon=True, name="ждёт-выключения").start()


# Клавиши, которые можно назначить и без Ctrl/Shift/Alt (Макс, 01.11): они почти нигде не нужны.
# Буквы, цифры, пробел — только с Ctrl/Shift/Alt: клавишу поиска программа занимает во всей Windows,
# и одиночная буква перестала бы печататься (а W в игре — ходить).
SPECIAL_KEYS = {"insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "pause": 0x13,
                "scrolllock": 0x91, **{f"num{i}": 0x60 + i for i in range(10)}}
SPECIAL_NAMES = {"insert": "Insert", "home": "Home", "end": "End", "pageup": "PageUp", "pagedown": "PageDown",
                 "pause": "Pause", "scrolllock": "ScrollLock", **{f"num{i}": f"Num{i}" for i in range(10)}}


def solo_ok(vk):
    """Можно ли эту клавишу назначить одну, без Ctrl/Shift/Alt: F1–F24, Insert, Home, End, PgUp/PgDn,
    Pause, Scroll Lock, цифры правой клавиатуры."""
    return 0x70 <= vk <= 0x87 or vk in SPECIAL_KEYS.values()


def parse_hotkey(text):
    """«Ctrl+Shift+O», «F8», «Num5» → (модификаторы, код клавиши) для RegisterHotKey."""
    mods = {"ctrl": 0x2, "control": 0x2, "shift": 0x4, "alt": 0x1, "win": 0x8}
    m, vk = 0x4000, None   # 0x4000 — MOD_NOREPEAT: зажатая клавиша не срабатывает много раз
    for part in [p.strip().lower() for p in text.split("+") if p.strip()]:
        if part in mods:
            m |= mods[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x70 + int(part[1:]) - 1
        elif part.replace(" ", "") in SPECIAL_KEYS:
            vk = SPECIAL_KEYS[part.replace(" ", "")]
        else:
            raise ValueError(T(f"непонятная клавиша «{part}»", f"unknown key «{part}»"))
    if vk is None:
        raise ValueError(T("нет основной клавиши (буквы, цифры или F1–F24)", "no main key (letter, digit or F1–F24)"))
    if not m & 0x7 and not solo_ok(vk):     # без Ctrl/Shift/Alt — только F-клавиши и «редкие» (Win не в счёт)
        raise ValueError(T("одну букву или цифру нельзя — добавь Ctrl, Shift или Alt",
                           "a single letter or digit is not allowed — add Ctrl, Shift or Alt"))
    return m, vk


def listen_hotkey(text):
    """Горячая клавиша (работает и в игре): включает поиск меню, если он спит, и выключает, если идёт."""
    try:
        mods, vk = parse_hotkey(text)
    except ValueError as e:
        log.error("Горячая клавиша %r не подходит: %s", text, e)
        message(T(f"Горячая клавиша «{text}» не подходит: {e}.\n\nИсправь \"hotkey\" в settings.json.",
                  f"Hotkey «{text}» is not valid: {e}.\n\nFix \"hotkey\" in settings.json."))
        return
    from ctypes import wintypes

    def loop():
        u = ctypes.windll.user32
        if not u.RegisterHotKey(None, 1, mods, vk):
            log.error("Не удалось занять горячую клавишу %s — её уже использует другая программа", text)
            UI_MESSAGES.append((T("КЛАВИША ", "KEY ") + text.upper() + T(" ЗАНЯТА", " IS TAKEN"),
                            T("Поменяй \"hotkey\" в settings.json", "Change \"hotkey\" in settings.json"), 6))
            return
        log.info("Горячая клавиша %s — включить/выключить поиск меню", text)
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:   # WM_HOTKEY
                toggle_scan(f"Нажата {text}")

    threading.Thread(target=loop, daemon=True, name="горячая-клавиша").start()


def ammo_name(weapon, ammo):
    """Как назвать патроны: у дробовиков обычные — это картечь (так в игре)."""
    if ammo == "FMJ" and (WEAPONS.get(weapon) or [None, None])[1] == "Shotgun":
        return T("картечь", "buckshot")
    return AMMO_RU.get(ammo)


def plate_model(state, settings):
    """Плашка для OBS — те же данные, что рисует окно поверх игры (OverlayWindow.render), уже посчитанные
    и на языке программы: страница overlay.html их только рисует (раньше считала сама и отставала).
    None — плашку не показывать."""
    w = state and WEAPONS.get(state.get("weapon"))
    if not w:
        return None
    weapon, tab = state["weapon"], state.get("tab")
    names = list(state.get("attachments") or [])
    detail = bool(settings.get("detail"))
    smith = bool(settings.get("gunsmith"))
    mode = int(settings.get("gunsmith_mode") or 0) % len(GUNSMITH_MODES)
    only = None if detail else KEY_STATS
    base = (CLASS_RU.get(w[1], w[1] or "").lower(), w[2] and f"{T('Урон', 'Damage')} {w[2]}",
            w[3] and w[3].replace("rpm", T("в/мин", "rpm")),
            w[4] and f"{T('Дальн.', 'Range')} {w[4].replace(' m', T(' м', ' m'))}")
    out = {"lang": LANG["code"], "weapon": w[0].upper(), "detail": detail, "detail_text": T("Подробно", "Details"),
           "base": "  ·  ".join(b for b in base if b), "lines": [], "stats": None, "smith": None, "ttk": None,
           "data": T("Данные: metaforge.app", "Data: metaforge.app"),
           "empty": T("Обвесы не меняют статы", "Attachments don't change stats")}
    view = compare_view(state) if tab != "ammo" else None
    gone, came = (view[2], view[3]) if view else ((), ())
    for n in names + [g for g in gone if g not in names]:
        a = ATTS.get(n)
        if a:
            out["lines"].append({"slot": SLOT_RU.get(a[1], a[1]), "text": a[0],
                                 "st": "gone" if n in gone else "came" if n in came else ""})
    if not names and not gone:
        if tab != "ammo":
            out["lines"].append({"slot": "", "text": T("Без обвесов", "No attachments"), "st": "dim"})
        elif any(weapon in a[3] and a[1] in MAG_TYPES for a in ATTS.values()):
            out["lines"].append({"slot": "", "text": T("Магазин ещё не выбран", "No magazine chosen yet"), "st": "dim"})
    ammo = state.get("ammo")
    if ammo in AMMO_RU or tab == "ammo":
        out["lines"].append({"slot": T("ПАТРОНЫ", "AMMO"),
                             "text": ammo_name(weapon, ammo) or T("ещё не выбраны", "not chosen yet"),
                             "st": "" if ammo else "dim"})
        if tab == "ammo" and ammo in AMMO_NOTE:
            out["lines"].append({"slot": "", "text": AMMO_NOTE[ammo], "st": "note"})

    def pct_text(m, val=None):
        if val:
            return val
        pct = round((m - 1) * 100)
        return f"{'+' if pct > 0 else '−' if pct < 0 else '±'}{abs(pct)}%"

    def tone(m, better):
        if better is None:
            return "text"
        if abs(m - 1) < 0.005:
            return "dim"
        return "good" if (m < 1) == (better == "low") else "bad"

    def bar(m, better, scale):
        """Полоска: + вправо (лучше), − влево (хуже); доля половины полоски."""
        if better is None or abs(m - 1) < 0.005:
            return 0
        return max(-1.0, min(1.0, (m - 1) * 100 * (-1 if better == "low" else 1) / scale))

    advice, advised = gunsmith_advice(weapon, names, mode) if smith else ([], None)
    if view:
        rows = compute_compare(view[0], view[1], weapon, only)
        out["stats"] = {"kind": "compare",
                        "head": [T("С ЗАПОМНЕННОЙ СБОРКОЙ", "VS SAVED BUILD"), T("БЫЛО", "WAS"), T("СЕЙЧАС", "NOW")],
                        "rows": [{"label": lb, "was": was, "now": now, "tone": "good" if v > 0 else "bad" if v < 0 else "dim"}
                                 for lb, was, now, v in rows]}
    elif smith:
        rows = compute_pair_rows(names, advised, weapon, only)
        out["stats"] = {"kind": "pair", "head": [T("СОВЕТ", "SUGGESTED"), T("СЕЙЧАС", "NOW")],
                        "rows": [{"label": lb, "now": pct_text(a, va), "tone": tone(a, better), "bar": bar(a, better, 30),
                                  "adv": pct_text(b, vb), "adv_on": pct_text(a, va) != pct_text(b, vb),
                                  "mark": bar(b, better, 30)} for lb, better, a, va, b, vb in rows]}
    else:
        rows = compute_rows(names, weapon, only)
        out["stats"] = {"kind": "plain", "rows": [
            {"label": lb,
             "now": (f"{m:.1f}×".replace(".0×", "×") if better is None else
                     (val or "±0%") if abs(m - 1) < 0.005 else pct_text(m, val)),
             "tone": "dim" if better is not None and abs(m - 1) < 0.005 else tone(m, better), "bar": bar(m, better, 40)}
            for lb, better, m, val in rows]}
    out["smith"] = {"on": smith, "title": T("ОРУЖЕЙНИК РЕКОМЕНДУЕТ", "GUNSMITH SUGGESTS"),
                    "modes": [m[0] for m in GUNSMITH_MODES], "mode": mode,
                    "advice": [{"slot": lb, "best": best, "ok": cur == best,
                                "now": T(f"сейчас: {cur}", f"now: {cur}") if cur and cur != best else ""}
                               for lb, best, cur in advice],
                    "nothing": T("для этой пушки советовать нечего", "nothing to suggest for this weapon") if smith and not advice else "",
                    "hint": T("прицел и магазин — на твой вкус", "sight and magazine — your choice")}
    if state.get("ttk"):
        buttons, hold = ttk_distances(weapon)
        chosen = (settings.get("ttk_dist") or {}).get(weapon)
        dist = chosen if buttons and chosen in buttons else (buttons[0] if buttons else TTK_DIST)
        ttk = ttk_payload(weapon, tab, ammo, dist)
        if ttk:
            fastest = [min((r[1][i][1] for r in ttk["rows"] if r[1][i][1] is not None), default=None)
                       for i in range(len(ttk["cols"]))]

            def cell(i, shots, secs):
                if shots is None:
                    return None
                return [f"{shots}×", T(f"{secs:.2f} с".replace(".", ","), f"{secs:.2f} s"),
                        bool(ttk.get("best") and secs == fastest[i])]

            ttk.update(buttons=buttons or [], unit=T("м", "m"),
                       hold=T(f"Урон держит до {hold} м", f"Damage holds up to {hold} m") if hold else "",
                       rows=[[lb, [cell(i, sh, sec) for i, (sh, sec) in enumerate(cells)], sel]
                             for lb, cells, sel in ttk["rows"]])
            out["ttk"] = ttk
    return out


def publish(st):
    """Отдать плашке (окну и OBS) то, что показываем: сборку и запомненную сборку (для сравнения)."""
    if st and st.get("weapon"):
        tab = st.get("tab")
        server.set_state(st["weapon"], st["attachments"], None if tab == "ammo" else SAVED.get(st["weapon"]),
                         ttk_payload(st["weapon"], tab, st.get("ammo")), tab, st.get("ammo"))
    else:
        server.set_state(None, [])


def toggle_saved():
    """Запомнить сборку, которая сейчас на плашке (дальше плашка показывает разницу с ней), или сбросить."""
    st = STATUS["shown"]
    if not st or not st.get("weapon") or st.get("tab") == "ammo":
        return
    w = st["weapon"]
    if w in SAVED:
        del SAVED[w]
        log.info("%s — сравнение со сборкой %s сброшено", COMPARE_KEY["text"], w)
        ev(T(f"Сравнение сборок {w} сброшено", f"Build comparison for {w} reset"), "dim")
    else:
        SAVED[w] = list(st["attachments"])
        log.info("%s — запомнил сборку для сравнения: %s", COMPARE_KEY["text"], describe_build(st))
        ev(T("Запомнил сборку для сравнения — ", "Build saved for comparison — ") + describe_build(st), "ok")
    publish(st)


def listen_compare_key(text):
    """Клавиша «запомнить сборку». Во всей Windows её НЕ занимаем (в других программах Ctrl+Shift+S —
    «Сохранить как…»): просто раз в 30 мс смотрим, зажата ли она, и только пока на экране плашка."""
    try:
        mods, vk = parse_hotkey(text)
    except ValueError as e:
        log.error("Клавиша сравнения %r не подходит: %s", text, e)
        return
    COMPARE_KEY["text"] = text
    need = [k for bit, k in ((0x2, 0x11), (0x4, 0x10), (0x1, 0x12), (0x8, 0x5B)) if mods & bit]

    def loop():
        u = ctypes.windll.user32
        was = False
        while not server.quit_event.is_set():
            if not STATUS["shown"]:
                was = False
                time.sleep(0.25)
                continue
            down = bool(u.GetAsyncKeyState(vk) & 0x8000) and all(u.GetAsyncKeyState(k) & 0x8000 for k in need)
            if down and not was:
                toggle_saved()
            was = down
            time.sleep(0.03)

    threading.Thread(target=loop, daemon=True, name="клавиша-сравнения").start()
    log.info("Клавиша %s — запомнить сборку / сбросить сравнение (пока видна плашка)", text)


# ---------------------------------------------------------------- консольное меню
def toggle_scan(source):
    hk = SCAN.get("hotkey", "")
    if SCAN["active"].is_set():
        SCAN["active"].clear()
        log.info("%s — выключаю поиск", source)
        ev(T("Поиск выключен", "Search off"), "dim")
        UI_MESSAGES.append((T("ОВЕРЛЕЙ: ПОИСК ВЫКЛЮЧЕН", "OVERLAY: SEARCH OFF"), T(f"Включить — {hk}", f"Turn on — {hk}"), 1.5))
    else:
        SCAN["active"].set()
        log.info("%s — включаю поиск", source)
        ev(T("Поиск включён — открой «Модули»", "Search on — open «ATTC.»"), "ok")
        UI_MESSAGES.append((T("ОВЕРЛЕЙ: ИЩУ МЕНЮ «МОДУЛИ»", "OVERLAY: LOOKING FOR THE ATTC. MENU"),
                            T(f"Выключить — {hk}", f"Turn off — {hk}"), 2.5))


# Окно консольного меню: чёрный фон, белые буквы, одна рамка на всё окно.
IW = 50                    # ширина внутри рамки (символов)
CON_W, CON_H = IW + 6, 20  # размер окна консоли


def enable_console_ui():
    """Чёрный фон, белые буквы, аккуратный размер окна, без мигающего курсора."""
    k = ctypes.windll.kernel32
    h = k.GetStdHandle(-11)
    mode = ctypes.c_uint32()
    if k.GetConsoleMode(h, ctypes.byref(mode)):
        k.SetConsoleMode(h, mode.value | 0x0004)   # ENABLE_VIRTUAL_TERMINAL_PROCESSING (для перерисовки)
    os.system("color 0F")                          # 0 — чёрный фон, F — белые буквы
    os.system(f"mode con: cols={CON_W} lines={CON_H}")
    sys.stdout.write("\x1b[?25l\x1b[2J")           # спрятать курсор, очистить экран
    sys.stdout.flush()


def _fit(text, width):
    return text if len(text) <= width else text[:width - 1] + "…"


def draw_screen(hk, note=None, note_kind=None):
    def row(text=""):
        return "  │" + _fit(text, IW).ljust(IW) + "│"

    def status(label, value):
        return row(f"    {label:<11}{_fit(value, IW - 15)}")

    line = "─" * IW
    if SCAN["active"].is_set():
        scan = "●  идёт"
    else:
        scan = f"○  спит ({hk} или 1)"
    game = {None: "○  ещё не искал", True: "●  найдена", False: "×  не найдена — запусти Wardogs"}[STATUS["game"]]
    st = STATUS["shown"]
    plate = f"»  {st['weapon']} · {len(st['attachments'])} обв." if st else "—  пусто"

    keys_left = ("[1] Поиск вкл/выкл", "[3] Настройки")
    keys_right = ("[2] Обновить базу", "[4] Журнал событий")
    out = [
        "",
        "  ┌" + line + "┐",
        row("WARDOGS  ·  ОВЕРЛЕЙ".center(IW)),
        "  ├" + line + "┤",
        row(),
        status("Поиск", scan),
        status("Игра", game),
        status("Плашка", plate),
        row(),
        "  ├" + line + "┤",
        row(),
        row(f"     {keys_left[0]:<24}{keys_right[0]}"),
        row(f"     {keys_left[1]:<24}{keys_right[1]}"),
        row("[0] Выход".center(IW)),
        row(),
        "  └" + line + "┘",
        f"{hk} — включить поиск прямо из игры".center(CON_W - 1),
        (_fit(note, CON_W - 3).center(CON_W - 1) if note else ""),
    ]
    sys.stdout.write("\x1b[H" + "\n".join(s + "\x1b[K" for s in out) + "\x1b[J")
    sys.stdout.flush()


def console_menu(settings, rec):
    """Консольное меню: одна клавиша без Enter, экран сам обновляется раз в секунду.
    Закрыть окно = выход (память сохранится)."""
    import msvcrt
    hk = settings.get("hotkey", "Ctrl+Shift+O")
    events_path = EVENTS_FILE
    settings_path = os.path.join(HERE, "settings.json")
    interactive = sys.stdin is not None and sys.stdin.isatty()
    if interactive:
        enable_console_ui()
    note = {"text": None, "kind": "warn", "until": 0.0}

    def say(text, kind="warn", secs=6):
        note.update(text=text, kind=kind, until=time.time() + secs)

    def redraw():
        if not interactive:
            return
        text = note["text"] if time.time() < note["until"] else None
        draw_screen(hk, text, note["kind"])

    def read_key():
        """Клавиша: в настоящем окне — сразу, без Enter; если ввод перенаправлен (проверки) — строкой."""
        if interactive:
            if msvcrt.kbhit():
                return msvcrt.getwch()
            return None
        line = sys.stdin.readline()
        if not line:
            raise EOFError
        return line.strip().lstrip("﻿")[:1]

    def update_db():
        say("Обновляю базу с MetaForge…", "info", 60)
        redraw()
        if update_db_now(rec)[0]:
            say("Готово. Что изменилось — в update_database.log", "ok")
        else:
            say("Подробности — в update_database.log", "err")

    def edit_settings():
        before = os.path.getmtime(settings_path) if os.path.exists(settings_path) else 0
        say("Открыл настройки в Блокноте — сохрани и закрой его", "info", 3600)
        redraw()
        subprocess.run(["notepad.exe", settings_path])
        if not os.path.exists(settings_path) or os.path.getmtime(settings_path) == before:
            say("Настройки не менялись", "dim")
            return False
        try:
            json.load(open(settings_path, encoding="utf-8-sig"))
        except Exception as e:
            ev("В settings.json ошибка — изменения не применены", "err")
            say(f"Ошибка: {e}"[:CON_W - 6], "err", 15)
            log.error("settings.json после правки не читается: %s", e)
            return False
        log.info("Меню: настройки изменены — перезапуск")
        return True

    last_draw = 0.0
    while not server.quit_event.is_set():
        try:
            ch = read_key()
        except (EOFError, KeyboardInterrupt):
            return
        if ch is not None:
            log.debug("Меню: клавиша %r", ch)
            if ch == "1":
                toggle_scan("Меню: [1]")
            elif ch == "2":
                update_db()
            elif ch == "3":
                if edit_settings():
                    RESTART["now"] = True
                    server.quit_event.set()
                    break
            elif ch == "4":
                if not os.path.exists(events_path):
                    open(events_path, "a", encoding="utf-8").close()
                os.startfile(events_path)
                say("Журнал событий открыт в Блокноте", "dim", 3)
            elif ch == "0":
                log.info("Меню: выход")
                server.quit_event.set()
                break
            elif ch.strip():
                say("Нажми 1, 2, 3, 4 или 0", "warn", 3)
            last_draw = 0.0
        if EVENTS_CHANGED.is_set() or time.time() - last_draw >= 1.0:
            EVENTS_CHANGED.clear()
            redraw()
            last_draw = time.time()
        if interactive:
            time.sleep(0.05)
    if interactive:
        word = "Перезапускаю…" if RESTART["now"] else "Выхожу…"
        sys.stdout.write(f"\x1b[2J\x1b[H\n   {word}\x1b[?25h\n")
        sys.stdout.flush()


RESTART = {"now": False, "settings": False}


def on_console_close(rec):
    """Окно консоли закрыли крестиком — успеваем сохранить память."""
    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)
    def handler(event):
        if event in (0, 2, 5, 6):   # Ctrl+C, закрытие окна, выход из Windows, выключение
            try:
                log.info("Окно консоли закрывается — сохраняю память")
                rec.save(force=True)
            except Exception:
                pass
            server.quit_event.set()
            return event == 0        # Ctrl+C гасим сами (программа выйдет), остальное — как обычно
        return False
    _handles.append(handler)
    ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)


def message(text):
    """Окошко с сообщением (когда консоли нет, print никто не увидит)."""
    log.info("Сообщение пользователю: %s", text.replace("\n", " "))
    ctypes.windll.user32.MessageBoxW(None, text, "Wardogs оверлей", 0x40)


def setup_log(filename, console_log=True):
    """Журнал пишется ВСЕГДА в файл (подробно) и, если есть консоль, — в неё (главное).
    Ошибки, которые никто не поймал (в том числе в фоновых потоках), тоже попадают в журнал."""
    if sys.stdout is None:   # pythonw — консоли нет
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
        console = False
    else:
        console = True
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    log.setLevel(logging.DEBUG)
    fh = logging.handlers.RotatingFileHandler(os.path.join(HERE, filename), maxBytes=LOG_MAX_BYTES,
                                              backupCount=1, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-7s [%(threadName)s] %(message)s",
                                      "%Y-%m-%d %H:%M:%S"))
    log.addHandler(fh)
    if console and console_log:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        log.addHandler(ch)
    sys.excepthook = lambda t, v, tb: log.critical("Необработанная ошибка", exc_info=(t, v, tb))
    threading.excepthook = lambda a: log.critical("Необработанная ошибка в потоке %s", a.thread.name,
                                                  exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    threading.current_thread().name = "главный"


if __name__ == "__main__":
    main()
