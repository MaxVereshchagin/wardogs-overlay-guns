"""Прогон оверлея по всем вариантам картинки.

Берёт настоящие скриншоты меню (tests\\screens) и делает из каждого десятки вариантов:
разрешения (720p…4K, 16:10, 21:9, 32:9), игра в окне, масштаб интерфейса, сжатие, размытие,
яркость/гамма/контраст, «Ночной свет», насыщенность, шум, курсор поверх надписи — и сочетания.
Плюс «ловушки» — экраны без меню, где программа не должна ничего показать.

Запуск:   python\\python.exe -I tests\\run_tests.py          (все варианты)
          python\\python.exe -I tests\\run_tests.py 3.webp   (только один скриншот)
Итог:     tests\\report.txt (таблица + разбор ошибок), подробности — app\\overlay_test.log

Оценка каждого варианта:
  OK      — пушка и все обвесы верные
  ПРОПУСК — пушка верная, лишних нет, но какой-то обвес не узнан (на плашке будет меньше строк)
  ОШИБКА  — не та пушка или ЛИШНИЙ/НЕВЕРНЫЙ обвес (на плашке были бы неправильные цифры) — самое опасное
  НЕТ МЕНЮ — меню не нашлось вовсе
"""
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")
SCREENS = os.path.join(HERE, "screens")
sys.path.insert(0, APP)
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import wardogs_overlay as wo  # noqa: E402

# Правильные ответы (проверены по скриншотам и подсказкам игры)
EXPECTED = {
    "1.webp": ("Galil", {"Galil 50 RND Magazine", "3x Tactical Prism Scope", "CQR Tactical Front Rail Grip",
                         "AR Multi-Caliber Suppressor"}),
    "2.webp": ("BMR-308", {"FAL 30 RND Magazine", "Spectr 4x", "Mini Angled Foregrip", "BMR-308 Flash Hider"}),
    "3.webp": ("AK74", {"AK74 30 RND Magazine", "Four Reticle Reflex", "RK6 Tactical Foregrip", "PBS-4 Suppressor"}),
    "4.webp": ("AK74", {"AK74 30 RND Magazine", "Compact P-1 Red Dot", "Mini Angled Foregrip", "PBS-4 Suppressor"}),
    "5.webp": ("AK74", {"AK74 75 RND Drum Magazine", "Spitfire 3X", "RVG Vertical Foregrip", "PBS-4 Suppressor"}),
    "6.webp": ("Galil", {"Galil 50 RND Magazine", "CQ-2x Prism Combat Scope", "CQR Tactical Front Rail Grip",
                         "Hexagon Brake"}),
    "7.webp": ("Evo-3 Ultra", {"Evo-3 Ultra 50 RND Box", "CQ-2x Prism Combat Scope", "Shift Foregrip",
                               "Three Port Brake"}),
    "8.webp": ("SKS", {"Kobra Reflex", "SKS Bipod", "DTK-1 Brake"}),
    "12.webp": ("AMR 50", {"6-10x Scope MOA"}),
    # с деньгами: длинные ценники «$112», «$790», «$800» над иконками (раньше закрывали часть иконки)
    "13_live.jpg": ("Galil", {"Galil 35 RND Magazine", "Spitfire 3X", "AFG Angled Foregrip"}),
}
# одинаковые по иконке и статам — считаем одним и тем же
SAME = {"6-10x Scope MRAD": "6-10x Scope MOA"}
# снимок «игра + OBS» (игра маленькая, ~0.52): знаем пушку и что обвесы — только из этого набора
PARTIAL = {"11.webp": ("Super-45", {"Holographic Sight", "Rubberized Ergonomic Foregrip",
                                    "SG Multi-Caliber Suppressor", "Super-45 13 RND Magazine",
                                    "Super-45 30 RND Magazine", "Super-45 40 RND Drum Magazine"})}
NEGATIVE = ["9.png", "10.png", "neg_now.png", "neg_all.png"]

# ---------------------------------------------------------------- варианты картинки (OpenCV + numpy, без Pillow)
BG = (14, 14, 14)


def base_1080(img):
    """Обрезанные скриншоты (1917×1079 и т.п.) дополняем до 1920×1080 тёмным."""
    h, w = img.shape[:2]
    if (w, h) == (1920, 1080):
        return img
    c = np.full((1080, 1920, 3), BG, np.uint8)
    c[:min(h, 1080), :min(w, 1920)] = img[:1080, :1920]
    return c


def resize(img, w, h):
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_LANCZOS4)


def boxed(img, W, H, scale=None, pos=None, bg=BG):
    """Картинка игры внутри экрана W×H: по центру с полосами (16:10, 21:9) или окном в позиции pos."""
    ih, iw = img.shape[:2]
    if scale is None:
        scale = min(W / iw, H / ih)
    w, h = int(iw * scale), int(ih * scale)
    c = np.full((H, W, 3), bg, np.uint8)
    x, y = pos if pos is not None else ((W - w) // 2, (H - h) // 2)
    part = resize(img, w, h)[:H - y, :W - x]
    c[y:y + part.shape[0], x:x + part.shape[1]] = part
    return c


def _u8(a):
    return np.clip(a, 0, 255).astype(np.uint8)


def jpeg(img, q):
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, q])
    return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def blur(img, sigma):
    return cv2.GaussianBlur(img, (0, 0), sigma)


def brightness(img, f):
    return _u8(img.astype(np.float32) * f)


def contrast(img, f):
    mean = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY).mean()
    return _u8(mean + (img.astype(np.float32) - mean) * f)


def saturation(img, f):
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY).astype(np.float32)[..., None]
    return _u8(gray + (img.astype(np.float32) - gray) * f)


def gamma(img, g):
    return _u8(((img.astype(np.float32) / 255.0) ** g) * 255)


def warm(img):   # «Ночной свет» Windows
    a = img.astype(np.float32)
    a[..., 1] *= 0.92
    a[..., 2] *= 0.75
    return _u8(a)


def noise(img, sigma):
    return _u8(img.astype(np.float32) + np.random.default_rng(1).normal(0, sigma, img.shape))


def cursor(img, s=1.0):
    """Курсор мыши прямо на надписи «МОДУЛИ» и на ячейке прицела."""
    c = img.copy()
    for x, y in ((190 * s, 445 * s), (370 * s, 520 * s)):
        pts = np.array([(x, y), (x, y + 19 * s), (x + 5 * s, y + 15 * s), (x + 9 * s, y + 23 * s),
                        (x + 12 * s, y + 21 * s), (x + 8 * s, y + 14 * s), (x + 14 * s, y + 14 * s)], np.int32)
        cv2.fillPoly(c, [pts], (255, 255, 255))
        cv2.polylines(c, [pts], True, (0, 0, 0), 1)
    return c


def variants(b):
    """b — скриншот 1920×1080 (RGB). Возвращает [(название, картинка)]."""
    b1440 = resize(b, 2560, 1440)
    v = [
        # --- разрешения и форматы экрана
        ("1080p", b),
        ("720p 1280x720", resize(b, 1280, 720)),
        ("ноутбук 1366x768", resize(b, 1366, 768)),
        ("900p 1600x900", resize(b, 1600, 900)),
        ("16:10 1680x1050", boxed(b, 1680, 1050)),
        ("16:10 1920x1200", boxed(b, 1920, 1200)),
        ("1440p", b1440),
        ("16:10 2560x1600", boxed(b, 2560, 1600)),
        ("4K", resize(b, 3840, 2160)),
        ("21:9 2560x1080", boxed(b, 2560, 1080)),
        ("21:9 3440x1440", boxed(b, 3440, 1440)),
        ("32:9 5120x1440", boxed(b, 5120, 1440)),
        # --- игра в окне / масштаб интерфейса
        ("окно 1280x720 на 1080p", boxed(b, 1920, 1080, 1280 / 1920, (300, 160), (40, 60, 90))),
        ("окно 1024x576 на 1080p", boxed(b, 1920, 1080, 1024 / 1920, (60, 400), (40, 60, 90))),
        ("окно 1600x900 на 1440p", boxed(b, 2560, 1440, 1600 / 1920, (500, 200), (40, 60, 90))),
        ("масштаб UI 0.9", boxed(b, 1920, 1080, 0.9, (40, 30))),
        ("масштаб UI 1.27 на 1440p", boxed(b, 2560, 1440, 1.27, (0, 0))),
    ]
    # --- искажения картинки — на 1080p и 1440p
    for name, img in (("1080p", b), ("1440p", b1440)):
        s = img.shape[1] / 1920
        v += [
            (f"{name} + сжатие 50", jpeg(img, 50)),
            (f"{name} + сильное сжатие 25", jpeg(img, 25)),
            (f"{name} + размытие", blur(img, 0.8 * s)),
            (f"{name} + темнее 15%", brightness(img, 0.85)),
            (f"{name} + ярче 15%", brightness(img, 1.15)),
            (f"{name} + гамма 0.8", gamma(img, 0.8)),
            (f"{name} + гамма 1.25", gamma(img, 1.25)),
            (f"{name} + контраст 0.85", contrast(img, 0.85)),
            (f"{name} + «Ночной свет»", warm(img)),
            (f"{name} + насыщенность 1.4", saturation(img, 1.4)),
            (f"{name} + шум", noise(img, 4)),
            (f"{name} + курсор на надписи", cursor(img, s)),
            (f"{name} + блёклый (HDR)", brightness(contrast(img, 0.75), 1.1)),
        ]
    # --- сочетания
    v += [
        ("720p + гамма 1.25", gamma(resize(b, 1280, 720), 1.25)),
        ("1440p + сжатие 60", jpeg(b1440, 60)),
        ("4K + размытие", blur(resize(b, 3840, 2160), 1.6)),
        ("900p + «Ночной свет»", warm(resize(b, 1600, 900))),
        ("21:9 3440 + сжатие 50 + курсор", jpeg(cursor(boxed(b, 3440, 1440), 1440 / 1080), 50)),
    ]
    return v


# ---------------------------------------------------------------- прогон
def run_one(rec, loc, a):
    hit = loc.search(wo._gray(a), wo.default_scales(*a.shape[:2]))
    if not hit:
        return None
    res = rec.analyze(wo.to_reference(a, *hit), native=(a,) + hit)
    if not res or not res.get("weapon"):
        return {"weapon": None, "attachments": [], "raw": (res or {}).get("raw")}
    return res


def norm_set(names):
    return {SAME.get(n, n) for n in names}


def judge(res, weapon, expected, partial=False):
    if res is None:
        return "НЕТ МЕНЮ", ""
    got = norm_set(res.get("attachments", []))
    if res.get("weapon") != weapon:
        return "ОШИБКА", f"пушка {res.get('weapon')!r} (прочитано {res.get('raw')!r}), нужна {weapon}"
    extra = got - expected
    if extra:
        return "ОШИБКА", f"лишние/неверные: {sorted(extra)}"
    if partial:
        return "OK", ""
    missing = expected - got
    if missing:
        return "ПРОПУСК", f"не узнаны: {sorted(missing)}"
    return "OK", ""


def main():
    only = set(sys.argv[1:])
    wo.setup_log("overlay_test.log")
    wo.log.setLevel("INFO")
    rec = wo.Recognizer(0.7, use_disk_cache=False)
    loc = wo.Locator()
    rows, t_all = [], time.time()

    cases = [(f, *EXPECTED[f], False) for f in EXPECTED] + [(f, *PARTIAL[f], True) for f in PARTIAL]
    for fname, weapon, expected, partial in cases:
        if only and fname not in only:
            continue
        img = wo.read_rgb(os.path.join(SCREENS, fname))
        vs = [("как есть", img)] if partial else variants(base_1080(img))
        for vname, v in vs:
            t = time.time()
            res = run_one(rec, loc, v)
            verdict, why = judge(res, weapon, norm_set(expected), partial)
            dt = time.time() - t
            rows.append((fname, vname, verdict, why, dt))
            print(f"{verdict:8} {fname:8} {vname:34} {dt:5.2f}s {why}", flush=True)

    for fname in NEGATIVE:
        if only and fname not in only:
            continue
        img = wo.read_rgb(os.path.join(SCREENS, fname))
        for vname, v in (("как есть", img), ("1440p", resize(img, 2560, 1440))):
            t = time.time()
            res = run_one(rec, loc, v)
            ok = res is None or not res.get("weapon")
            verdict, why = ("OK", "") if ok else ("ОШИБКА", f"нашёл меню там, где его нет: {res}")
            rows.append((fname, "ловушка: " + vname, verdict, why, time.time() - t))
            print(f"{verdict:8} {fname:8} {'ловушка: ' + vname:34} {why}", flush=True)

    # ---- отчёт
    from collections import Counter, defaultdict
    total = Counter(r[2] for r in rows)
    by_variant = defaultdict(Counter)
    for f, vn, verdict, why, dt in rows:
        key = vn.split(" + ")[0] if " + " in vn and not vn.startswith(("720p +", "1440p + сж", "4K +", "900p +", "21:9 3440 +")) else vn
        by_variant[vn][verdict] += 1
    lines = [f"Прогон {time.strftime('%d.%m.%Y %H:%M')}: {len(rows)} проверок за {time.time() - t_all:.0f} с",
             "Итог: " + ", ".join(f"{k} — {v}" for k, v in total.most_common()), ""]
    lines.append("По вариантам (сколько скриншотов в каждом исходе):")
    for vn, c in by_variant.items():
        lines.append(f"  {vn:38} " + "  ".join(f"{k}:{n}" for k, n in c.most_common()))
    bad = [r for r in rows if r[2] != "OK"]
    lines += ["", f"Не OK ({len(bad)}):"] + [f"  {v:8} {f:8} {vn:34} {why}" for f, vn, v, why, dt in bad]
    slow = sorted(rows, key=lambda r: -r[4])[:5]
    lines += ["", "Самые долгие (первое распознавание, дальше — из памяти):"] + \
             [f"  {dt:5.2f}s {f} {vn}" for f, vn, v, why, dt in slow]
    open(os.path.join(HERE, "report.txt"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines[:2]))
    print(f"Отчёт: {os.path.join(HERE, 'report.txt')}")


if __name__ == "__main__":
    main()
