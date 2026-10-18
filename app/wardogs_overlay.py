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
import os
import platform
import re
import subprocess
import sys
import threading
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import server  # noqa: E402

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
            parts.append(f"магазин {a[4] if len(a) > 4 and a[4] else (m.group(1) if m else '')}".strip())
        else:
            parts.append(n)
    return f"{st['weapon']}: " + (", ".join(parts) if parts else "без обвесов")


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
}


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
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "update_database.py")], cwd=HERE,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not os.path.exists(data):
        text = ("Не получилось скачать базу с MetaForge — нужен интернет при первом запуске.\n\n"
                "Подробности — в app\\update_database.log. Проверь интернет и запусти снова.")
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
        return data["W"], data["A"]
    except ValueError:   # самый первый формат (W и A отдельными кусками)
        W = json.loads(re.search(r"W:(\[.*?\]\]),\s*\n", js, re.S).group(1))
        A = json.loads(re.search(r"A:(\[.*\])\s*\};", js, re.S).group(1))
        return W, A


W_LIST, A_LIST = load_db()
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
    log.info("База перечитана: %d пушек, %d обвесов", len(WEAPONS), len(ATTS))
    return len(WEAPONS), len(ATTS)


def match_weapon(text):
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
    log.warning("Пушка не узнана: прочитано %r → %r (записал в unknown_names.txt)", text, n)
    ev(f"Не узнал пушку «{text}» — см. unknown_names.txt", "warn")
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
ICON_BOX = (96, 84)                # рамка, в которую игра вписывает иконку (ширина, высота) при 1080p


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
        ref = read_rgb(os.path.join(HERE, "ref_moduli.png")).astype(np.float32)
        self.header_ref = ref
        self._tpl_cache = {}
        self.last_top = []
        self._slot_last = {}         # (пушка, № ячейки) → какой обвес там был в последний раз (за этот запуск)
        self._hold = {}              # отпечаток ячейки → обвес, оставленный под курсором
        self._mag_icon_cache = {}    # отпечаток ячейки магазина → (магазин по иконке, точность, отрыв)
        self._ocr_session = {}       # прочитанный текст за этот запуск (и неудачные попытки тоже)
        self._miss = set()          # ячейки, где ничего не нашлось (только до закрытия программы)
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
        path = os.path.join(HERE, "icons", ICONS[name] + ".webp")
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

    def find_header(self, img, min_score=0.8):
        """Ищем «МОДУЛИ» — значит открыто меню сборки. Возвращает сдвиг по вертикали или None."""
        x, y = HEADER_POS
        band = img[max(0, y - 40): y + 62, x - 10: x + 125].astype(np.float32)
        r = cv2.matchTemplate(band, self.header_ref, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(r)
        if score < min_score:
            return None
        return (max(0, y - 40) + loc[1]) - y

    def analyze(self, img, native=None):
        """img — эталонный кадр 1920×1080. native — (исходный кадр, s, x, y надписи): оттуда читаем
        текст в родном разрешении, мелкие цифры «30/30» после пересчёта размываются.
        Возвращает {'weapon':..., 'attachments':[...]} или None."""
        if native is not None:
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
            button = warm(MODULES_BTN[1] - 4, MODULES_BTN[1] + 4, cx + MODULES_BTN[0] - 4, cx + MODULES_BTN[0] + 4)
            frame = (warm(179, 182, cx + 20, cx + 300) + warm(200, 330, cx, cx + 3)) / 2
            return button + 3 * frame
        scores = {cx: selected(cx) for cx in CARD_X}
        card = max(CARD_X, key=scores.get)

        x0, y0, x1, y1 = CARD_NAME
        weapon_text, wkey = text(card + x0, y0, card + x1, y1, 2)
        weapon = match_weapon(weapon_text)
        if not weapon:
            return {"weapon": None, "attachments": [], "raw": weapon_text}
        if wkey not in self.mem["ocr"]:
            log.info("Пушка: прочитано %r → %s (карточка %d; выбранность карточек: %s)", weapon_text, weapon,
                     CARD_X.index(card) + 1, [round(scores[c]) for c in CARD_X])
        self.remember("ocr", wkey, weapon_text)

        cands = [n for n, a in ATTS.items() if weapon in a[3] and n in ICONS]
        types = self.mem["slot_types"].setdefault(weapon, {})
        mag_cands = [n for n in cands if ATTS[n][1] in MAG_TYPES]
        mag_icon = None               # (магазин по иконке, точность, отрыв от второго магазина)
        found = []
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
            elif self._holds(weapon, i, best_name, best):
                # иконку частично закрывает курсор мыши: это тот же обвес, что был в ячейке только что,
                # и он всё ещё самый похожий — оставляем (иначе плашка мигает, пока водишь мышью)
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

        self.save()
        return {"weapon": weapon, "attachments": found}


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


def _gray(rgb):
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


class Locator:
    """Находит меню «Модули» на кадре. Место = (масштаб s, x, y надписи на экране)."""

    def __init__(self):
        ref = read_rgb(os.path.join(HERE, "ref_moduli.png"))
        self.ref = _gray(ref).astype(np.float32)
        self.ref_small = cv2.resize(self.ref, None, fx=COARSE, fy=COARSE, interpolation=cv2.INTER_AREA)
        # вторая опора: «ЯЧЕЙКИ ДЛЯ ЭКИПИРОВКИ» над заголовком (у кнопок «МОДУЛИ» её нет)
        cells = read_rgb(os.path.join(HERE, "ref_cells.png"))
        self.cells = _gray(cells).astype(np.float32)
        self.near = None
        self.coarse = (-1.0, None)

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

    def search(self, frame_gray, scales):
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
        return best[1:] if best else None


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
    return f"{x:.2f}".replace(".", ",")


def compute_rows(att_names, weapon=None):
    """Строки плашки: (подпись, что лучше, множитель, своё значение справа или None).
    Время прицеливания — в секундах: у пушки в базе есть родное время (AK74 — 0.45 с),
    обвесы его умножают. Для остальных статов родных секунд в базе нет — остаются проценты."""
    base_ads = None
    w = WEAPONS.get(weapon) if weapon else None
    if w and w[5]:
        m = re.match(r"([\d.]+)", w[5])
        base_ads = float(m.group(1)) if m else None
    # Эффект обвеса: [стат, значение] — умножить; [стат, значение, "="] — заменить; [.., "+"] — прибавить.
    # Итог как в игре: (родное или заменённое + прибавки) × множители.
    mult, over, add = {}, {}, {}
    for n in att_names:
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
    rows = []
    for key, ru, better in STATS:
        if key in total and abs(total[key] - 1) >= 0.005:
            value = None
            if key == "ADS time" and base_ads:
                new = round(base_ads * total[key], 2)
                diff = new - round(base_ads, 2)
                ru = f"Прицел. {_sec(base_ads)}→{_sec(new)} с"
                value = f"{'+' if diff > 0 else '−' if diff < 0 else '±'}{_sec(abs(diff))} с"
            rows.append((ru, better, total[key], value))
    return rows[:MAX_ROWS]


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

    def flash(self, title, subtitle, seconds=8):
        """Плашка «программа запущена» — висит несколько секунд, чтобы точно заметить."""
        c, W, pad = self.canvas, self.W, 16
        c.delete("all")
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

    def _click_through(self):
        # Окно не перехватывает мышь и не забирает фокус у игры.
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(self.root.winfo_id())
        GWL_EXSTYLE = -20
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= 0x80000 | 0x20 | 0x80 | 0x08000000  # LAYERED | TRANSPARENT | TOOLWINDOW | NOACTIVATE
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

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
        c = self.canvas
        c.delete("all")
        W, pad = self.W, 16
        c.create_rectangle(0, 0, 4, 2000, fill=ACCENT, width=0)
        y = 14
        c.create_text(pad, y, text=w[0].upper(), fill=TEXT, font=(FONT, 22, "bold"), anchor="nw")
        c.create_text(W - pad, y + 8, text=CLASS_RU.get(w[1], w[1] or ""), fill=ACCENT, font=(FONT, 10), anchor="ne")
        y += 38
        base = [w[2] and f"Урон {w[2]}", w[3] and w[3].replace("rpm", "в/мин"),
                w[4] and f"Дальн. {w[4].replace(' m', ' м')}", w[5] and f"Прицел. {w[5].replace('s', ' с')}"]
        c.create_text(pad, y, text="  ·  ".join(b for b in base if b), fill=DIM, font=(FONT, 10), anchor="nw")
        y += 26
        c.create_line(pad, y, W - pad, y, fill=LINE)
        y += 8
        atts = [ATTS[n] for n in state.get("attachments", []) if n in ATTS]
        if not atts:
            c.create_text(pad, y, text="Без обвесов", fill=DIM, font=(FONT, 11), anchor="nw")
            y += 22
        for a in atts:
            c.create_text(pad, y + 2, text=SLOT_RU.get(a[1], a[1]), fill=DIM, font=(FONT, 8), anchor="nw")
            c.create_text(pad + 78, y, text=a[0], fill=TEXT, font=(FONT, 11), anchor="nw")
            y += 22
        y += 4
        c.create_line(pad, y, W - pad, y, fill=LINE)
        y += 8
        rows = compute_rows(state.get("attachments", []), state.get("weapon"))
        if not rows:
            c.create_text(pad, y, text="Обвесы не меняют статы", fill=DIM, font=(FONT, 11), anchor="nw")
            y += 24
        bar_x0, bar_w = 200, 110
        for ru, better, m, value in rows:
            c.create_text(pad, y, text=ru, fill=TEXT, font=(FONT, 11), anchor="nw")
            if better is None:
                c.create_text(W - pad, y, text=f"{m:.1f}×".replace(".0×", "×"), fill=TEXT, font=(FONT, 11, "bold"), anchor="ne")
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
        y += 4
        c.create_text(pad, y, text="Данные: metaforge.app", fill=DIM, font=(FONT, 7), anchor="nw")
        y += 18
        c.config(height=y)
        self.root.geometry(f"{W}x{y}+{self.x}+{self.y}")
        if not self.shown:
            self.root.deiconify()
            self._click_through()
            self.shown = True


# ---------------------------------------------------------------- главный цикл
PLACEMENT = {"pos": None}   # где сейчас должно стоять окно (заполняет capture_loop)
SCAN = {"active": threading.Event()}   # идёт ли поиск меню (иначе программа спит)
STATUS = {"game": None, "menu": False, "shown": None}   # для строки состояния в консольном меню
UI_MESSAGES = []            # короткие плашки для окна («поиск включён» и т.п.) — из других потоков
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
                        ev("Игра найдена", "ok")
                    elif not a:
                        ev("Игра не найдена (не запущена или свёрнута)", "warn")
                    last_area = a
                if a is None:
                    where = None                      # игры нет или свёрнута
                else:
                    frame = grab(a)
                    if where:                         # быстро: меню там же, где было?
                        hit = loc.check(_gray(frame), *where)
                        if hit:
                            found = hit[1:]
                        else:
                            log.info("Меню «Модули» пропало")
                            ev("Меню «Модули» закрыто", "dim")
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
                        found = loc.search(_gray(frame), scales)
                        if found:
                            log.info("Меню «Модули» найдено: масштаб %.3f, надпись в (%d, %d) внутри снимка %dx%d; "
                                     "поиск %s за %.2f с", found[0], found[1], found[2], w, h,
                                     "полный" if full else f"по масштабу {scales}", time.time() - t_search)
                            ev("Меню «Модули» открыто", "ok")
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
                                ev("Поверх игры другое окно — плашку не показываю", "warn")
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
                    state = rec.analyze(to_reference(frame, s, hx, hy), native=(frame, s, hx, hy))
                    if time.time() - t_an > 0.3:
                        log.debug("Распознавание кадра заняло %.2f с", time.time() - t_an)
                    stats["menu"] += 1
            except Exception:
                log.exception("Ошибка распознавания")
                ev("Ошибка распознавания — подробности в журнале", "err")
                state = None
            STATUS["menu"] = bool(where)
            # ---- автоотключение: меню давно не видно — засыпаем, чтобы не тратить процессор
            if where:
                menu_seen_at = time.time()
            elif auto_off > 0 and time.time() - menu_seen_at >= auto_off:
                log.info("Меню «Модули» не видно %.0f с — выключаю поиск", auto_off)
                ev(f"Поиск уснул ({auto_off:.0f} с без меню)", "dim")
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
                if st:
                    server.set_state(st["weapon"], st["attachments"])
                    ev(describe_build(st), "ok")
                else:
                    server.set_state(None, [])
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


def main():
    testing = len(sys.argv) > 2 and sys.argv[1] == "--test"
    # в меню журнал на экран не выводим — там своё окно со статусом и лентой событий
    setup_log("overlay_test.log" if testing else "overlay.log", console_log="--menu" not in sys.argv)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # настоящие пиксели при масштабе Windows 125%/150%
    except Exception as e:
        log.warning("Не удалось включить точные пиксели (DPI): %s", e)
    settings = load_settings()
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
                hit = loc.search(_gray(img), default_scales(*img.shape[:2]))
                t_find = time.time() - t
                res = rec.analyze(to_reference(img, *hit), native=(img,) + hit) if hit else "меню не найдено"
                where = f"масштаб {hit[0]:.2f}, надпись в ({hit[1]:.0f},{hit[2]:.0f})" if hit else ""
                line = (f"{os.path.basename(p)} {img.shape[1]}x{img.shape[0]} {attempt}: "
                        f"поиск {t_find:.2f}s, всего {time.time() - t:.2f}s; {where}; {res}")
                log.info("Итог: %s", line)
                print(line)
        return

    menu = "--menu" in sys.argv          # консольная версия с меню («Wardogs Overlay.bat»)
    ok = take_single_instance()
    if not ok and "--restarted" in sys.argv:   # перезапуск из меню: ждём, пока старая копия закроется
        for _ in range(30):
            time.sleep(0.5)
            ok = take_single_instance()
            if ok:
                break
    if not ok:
        log.warning("Уже запущена другая копия — эта закрывается")
        message("Оверлей Wardogs уже запущен.\n\nЧтобы перезапустить — сначала выйди из него "
                "(пункт 0 в меню или закрой его окно).")
        return
    listen_for_quit()
    events_start()
    SCAN["hotkey"] = str(settings.get("hotkey", "Ctrl+Shift+O"))
    listen_hotkey(SCAN["hotkey"])
    if menu:
        ctypes.windll.kernel32.SetConsoleTitleW("Wardogs оверлей")
        print("\n Загружаю распознавание… (несколько секунд)", flush=True)

    log.info("Загружаю распознавание…")
    t0 = time.time()
    rec = Recognizer(settings["match_threshold"])
    log.info("Распознавание текста загружено за %.1f с", time.time() - t0)
    SCAN["active"].set()   # при запуске сразу ищем; нет меню — уснём через auto_off_seconds
    if menu:
        on_console_close(rec)
        threading.Thread(target=console_menu, args=(settings, rec), daemon=True, name="меню").start()
    if settings.get("obs_server"):
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
    log.info("Работаю. Открой в игре: Продавец → Экипировка → Модули. Выход — пункт 0 в меню.")

    try:
        if not settings["show_window"]:
            while not server.quit_event.wait(1):
                pass
        else:
            win = OverlayWindow(settings)
            win.flash("ОВЕРЛЕЙ WARDOGS ЗАПУЩЕН",
                      f"Искать меню — {settings.get('hotkey')} · закрыть — "
                      + "пункт 0 в окне программы")

            def tick():
                if server.quit_event.is_set():
                    log.info("Получил команду выключиться")
                    win.root.destroy()
                    return
                while UI_MESSAGES:   # «поиск включён/выключен» от горячей клавиши
                    title, sub, secs = UI_MESSAGES.pop(0)
                    win.flash(title, sub, secs)
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
    rec.save(force=True)
    log.info("Память сохранена (%d ячеек). Выключено.", len(rec.mem["slots"]))
    if RESTART["now"]:   # «6 — перезапустить» из меню: новая копия в этом же окне консоли
        for h in _handles:
            if isinstance(h, int):
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(h))
        subprocess.Popen([sys.executable, "-I", os.path.abspath(__file__), "--menu", "--restarted"], cwd=HERE)
        log.info("Запустил новую копию")


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

    def wait():
        k.WaitForSingleObject(h, 0xFFFFFFFF)
        log.info("Получил команду выключиться (снаружи)")
        server.quit_event.set()

    threading.Thread(target=wait, daemon=True, name="ждёт-выключения").start()


def parse_hotkey(text):
    """«Ctrl+Shift+O» → (модификаторы, код клавиши) для RegisterHotKey."""
    mods = {"ctrl": 0x2, "control": 0x2, "shift": 0x4, "alt": 0x1, "win": 0x8}
    m, vk = 0x4000, None   # 0x4000 — MOD_NOREPEAT: зажатая клавиша не срабатывает много раз
    for part in [p.strip().lower() for p in text.split("+") if p.strip()]:
        if part in mods:
            m |= mods[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x70 + int(part[1:]) - 1
        else:
            raise ValueError(f"непонятная клавиша «{part}»")
    if vk is None:
        raise ValueError("нет основной клавиши (буквы, цифры или F1–F24)")
    return m, vk


def listen_hotkey(text):
    """Горячая клавиша (работает и в игре): включает поиск меню, если он спит, и выключает, если идёт."""
    try:
        mods, vk = parse_hotkey(text)
    except ValueError as e:
        log.error("Горячая клавиша %r не подходит: %s", text, e)
        message(f"Горячая клавиша «{text}» не подходит: {e}.\n\nИсправь \"hotkey\" в settings.json.")
        return
    from ctypes import wintypes

    def loop():
        u = ctypes.windll.user32
        if not u.RegisterHotKey(None, 1, mods, vk):
            log.error("Не удалось занять горячую клавишу %s — её уже использует другая программа", text)
            UI_MESSAGES.append(("КЛАВИША " + text.upper() + " ЗАНЯТА", "Поменяй \"hotkey\" в settings.json", 6))
            return
        log.info("Горячая клавиша %s — включить/выключить поиск меню", text)
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:   # WM_HOTKEY
                toggle_scan(f"Нажата {text}")

    threading.Thread(target=loop, daemon=True, name="горячая-клавиша").start()


# ---------------------------------------------------------------- консольное меню
def toggle_scan(source):
    hk = SCAN.get("hotkey", "")
    if SCAN["active"].is_set():
        SCAN["active"].clear()
        log.info("%s — выключаю поиск", source)
        ev("Поиск выключен", "dim")
        UI_MESSAGES.append(("ОВЕРЛЕЙ: ПОИСК ВЫКЛЮЧЕН", f"Включить — {hk}", 1.5))
    else:
        SCAN["active"].set()
        log.info("%s — включаю поиск", source)
        ev("Поиск включён — открой «Модули»", "ok")
        UI_MESSAGES.append(("ОВЕРЛЕЙ: ИЩУ МЕНЮ «МОДУЛИ»", f"Выключить — {hk}", 2.5))


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
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "update_database.py")], cwd=HERE,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            n_w, n_a = reload_db()
            rec._tpl_cache.clear()
            rec._miss.clear()
            m = re.search(r"изменёнными статами: (\d+)", r.stdout or "")
            changed = m.group(1) if m else "?"
            ev(f"База обновлена: {n_w} пушек, {n_a} обвесов, изменено {changed}", "ok")
            say("Готово. Что изменилось — в update_database.log", "ok")
        else:
            ev("Обновить базу не получилось — старая не тронута", "err")
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


RESTART = {"now": False}


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
