"""Обновляет базу оверлея с MetaForge (пункт 2 в меню «Wardogs Overlay.bat»).

Что делает:
  1. Скачивает с metaforge.app три открытых файла данных:
       /api/wardogs/database          — все предметы: пушки, обвесы, иконки, что к чему подходит
       /api/wardogs/attachment-stats  — точные множители обвесов (из файлов игры)
       /api/wardogs/ballistics/v2     — урон, скорострельность, дальность (Season 2)
     Копии кладёт в metaforge_raw\\ (для разбора, если что-то пойдёт не так).
  2. Берёт только то, что продаётся у продавца, и переводит в формат программы.
  3. Сравнивает со старой базой и пишет в журнал, что изменилось.
  4. Старую базу сохраняет как wardogs_data.prev.js (ничего не удаляет), пишет новую wardogs_data.js
     и icons.json, докачивает новые иконки.
Журнал — update_database.log. После обновления перезапусти оверлей.
"""
import json
import logging
import os
import re
import shutil
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "metaforge_raw")
API = "https://metaforge.app/api/wardogs/"
ICON_URL = "https://static.metaforge.app/wardogs/icons/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WardogsOverlay", "Accept": "application/json"}

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)-7s %(message)s",
                    handlers=[logging.FileHandler(os.path.join(HERE, "update_database.log"), encoding="utf-8"),
                              logging.StreamHandler(sys.stdout)])
log = logging.getLogger("update")

# Атрибуты игры → названия статов в программе (подписи на плашке берутся из программы).
# Пристрелку (Zeroing) на плашке не показываем.
ATTR = {
    "AimTime": "ADS time",
    "VerticalRecoilMultiplier": "Vertical recoil",
    "HorizontalRecoilMultiplier": "Horizontal recoil",
    "SpreadMultiplier": "Spread",
    "EquipSpeedMultiplier": "Equip speed",
    "AdsZoomMultiplier": "ADS zoom",
    "Character.AimSwayScaleMultiplier": "Aim sway",
    "Character.AimSwaySpeedMultiplier": "Sway speed",
    "Character.AimSwayHorizontalMovementMultiplier": "Sway from horizontal movement",
    "Character.AimSwayVerticalMovementMultiplier": "Sway from vertical movement",
    "Character.WeaponSway.TranslationStabilizationMultiplier": "Translation stabilisation",
    "Character.WeaponSway.RotationStabilizationMultiplier": "Rotation stabilisation",
    "Character.WeaponSway.StabilizationTimeMultiplier": "Stabilisation time",
}
SKIP_ATTR = {"MaxZeroingRange", "MinZeroingRange"}
OPS = {"multiply": "x", "override": "=", "add": "+"}


def fetch(name):
    t = time.time()
    req = urllib.request.Request(API + name, headers=HEADERS)
    body = urllib.request.urlopen(req, timeout=60).read()
    os.makedirs(RAW, exist_ok=True)
    open(os.path.join(RAW, name.replace("/", "_") + ".json"), "wb").write(body)
    data = json.loads(body)
    log.info("Скачал %s: %d КБ за %.1f с (данные от %s)", name, len(body) // 1024, time.time() - t,
             data.get("generatedAt"))
    return data


def att_type(a):
    """Тип обвеса в понятиях программы (по нему программа знает, какая это ячейка)."""
    slot, name = a.get("slot"), a["name"].lower()
    if slot == "Sight":
        return "Optic"
    if slot == "Magazine":
        return "Magazine"
    if slot == "Muzzle":
        return "Suppressor" if "suppressor" in name else "Muzzle"
    if slot == "Underbarrel":
        return "Bipod" if ("bipod" in name or "grip pod" in name) else "Foregrip"
    return slot or "Other"


def effects_of(stats):
    """Множители обвеса. У сошек два состояния — берём «по умолчанию» (сложены), как в игре при сборке."""
    if not stats:
        return [], None
    mods = stats.get("modifiers")
    if mods is None and stats.get("states"):
        default = stats.get("defaultState")
        state = next((s for s in stats["states"] if s.get("key") == default), stats["states"][0])
        mods = state.get("modifiers", [])
    out = []
    for m in mods or []:
        attr = m.get("attribute")
        if attr in SKIP_ATTR:
            continue
        if attr not in ATTR:
            log.warning("Неизвестный атрибут %r (пропускаю) — может, стоит добавить в программу", attr)
            continue
        op = OPS.get(m.get("op"))
        if op is None:
            log.warning("Неизвестная операция %r у %r (пропускаю)", m.get("op"), attr)
            continue
        e = [ATTR[attr], m["value"]]
        if op != "x":
            e.append(op)
        out.append(e)
    return out, stats.get("capacity")


def load_old():
    path = os.path.join(HERE, "wardogs_data.js")
    if not os.path.exists(path):
        return None
    js = open(path, encoding="utf-8-sig").read()
    try:
        return json.loads(js[js.index("{"): js.rindex("}") + 1])
    except ValueError:   # старый формат (до обновлятора) — W и A отдельными кусками
        W = json.loads(re.search(r"W:(\[.*?\]\]),\s*\n", js, re.S).group(1))
        A = json.loads(re.search(r"A:(\[.*\])\s*\};", js, re.S).group(1))
        return {"W": W, "A": A}


def main():
    log.info("===== Обновление базы с MetaForge =====")
    db = fetch("database")
    stats = fetch("attachment-stats")["attachments"]
    ball = fetch("ballistics/v2")

    items = db["items"] if isinstance(db["items"], list) else list(db["items"].values())
    calibres = ball.get("calibres", {})
    ballistic = {w["slug"]: w for w in ball.get("weapons", [])}

    # ---- пушки (только те, что продаются; тестовые разработчиков отбрасываем)
    weapons, slug2name = [], {}
    for w in items:
        if w.get("tab") != "weapons" or not w.get("inVendor"):
            continue
        if w["name"] in {x[0] for x in weapons}:
            log.debug("Пушка %r встречается дважды — беру первую", w["name"])
            slug2name[w["slug"]] = w["name"]
            continue
        b = ballistic.get(w["slug"], {})
        attrs = w.get("attributes") or {}
        dmg = (calibres.get(b.get("calibre") or w.get("calibre") or "") or {}).get("baseDamage")
        rpm, rng, ads = b.get("roundsPerMinute"), b.get("effectiveRange"), attrs.get("AimTime")
        weapons.append([
            w["name"], w.get("subcategory"),
            str(round(dmg, 1)).removesuffix(".0") if dmg else None,
            f"{rpm} rpm" if rpm else None,
            f"{rng} m" if rng else None,
            f"{ads}s" if ads else None,
            f"${w['price']:,}" if w.get("price") else None,
        ])
        slug2name[w["slug"]] = w["name"]
    log.info("Пушек у продавца: %d", len(weapons))

    # ---- обвесы (только у продавца)
    atts, icons = [], {}
    for a in items:
        if a.get("tab") != "attachments" or not a.get("inVendor"):
            continue
        eff, cap = effects_of(stats.get(a["slug"]))
        fits = [slug2name[s] for s in a.get("fits", []) if s in slug2name]
        entry = [a["name"], att_type(a), eff, fits]
        if cap:
            entry.append(cap)
        atts.append(entry)
        if a.get("icon"):
            icons[a["name"]] = a["icon"]
        if a["slug"] not in stats:
            log.debug("У обвеса %r нет статов на сайте (видимо, ничего не меняет)", a["name"])
    atts.sort(key=lambda x: x[0].lower())
    log.info("Обвесов у продавца: %d, с иконками: %d", len(atts), len(icons))
    if len(weapons) < 10 or len(atts) < 50:
        log.error("Подозрительно мало данных — сайт, видимо, отдал что-то не то. Базу НЕ меняю.")
        return 1

    # ---- что изменилось
    old = load_old()
    if old:
        ow = {w[0]: w for w in old["W"]}
        oa = {a[0]: a for a in old["A"]}
        na = {a[0]: a for a in atts}
        added = sorted(set(na) - set(oa))
        removed = sorted(set(oa) - set(na))
        log.info("Новые пушки: %s", sorted({w[0] for w in weapons} - set(ow)) or "нет")
        log.info("Пропавшие пушки: %s", sorted(set(ow) - {w[0] for w in weapons}) or "нет")
        log.info("Новые обвесы: %s", added or "нет")
        log.info("Пропавшие обвесы: %s", removed or "нет")

        def norm_eff(e):
            return sorted((x[0], round(float(x[1]), 3), x[2] if len(x) > 2 else "x") for x in e)
        changed = 0
        for name in sorted(set(na) & set(oa)):
            # в старой базе зум записан как множитель — сравниваем без учёта вида операции у зума
            before = [(k, v, "=" if k == "ADS zoom" else op) for k, v, op in norm_eff(oa[name][2])]
            after = norm_eff(na[name][2])
            if before != after:
                changed += 1
                log.info("Изменился %s:\n      было  %s\n      стало %s", name, before, after)
        log.info("Обвесов с изменёнными статами: %d", changed)

    # ---- записываем (старую базу сохраняем рядом)
    data_path = os.path.join(HERE, "wardogs_data.js")
    if os.path.exists(data_path):
        shutil.copyfile(data_path, os.path.join(HERE, "wardogs_data.prev.js"))
        log.info("Старая база сохранена как wardogs_data.prev.js")
    payload = {"source": "https://metaforge.app/wardogs/database",
               "generatedAt": db.get("generatedAt"), "statsAt": time.strftime("%Y-%m-%d"),
               "W": weapons, "A": atts}
    with open(data_path, "w", encoding="utf-8") as f:
        f.write("// База Wardogs для оверлея. Собрана update_database.py с MetaForge — руками не править.\n")
        f.write("// Пушка: [название, класс, урон, скорострельность, дальность, время прицеливания, цена]\n")
        f.write("// Обвес: [название, тип, [[стат, значение, (операция: нет — умножить, '=' — заменить, '+' — прибавить)]],"
                " [на какие пушки], (ёмкость магазина)]\n")
        f.write("window.WARDOGS = ")
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    json.dump(icons, open(os.path.join(HERE, "icons.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    log.info("Записал wardogs_data.js и icons.json")

    # ---- иконки
    os.makedirs(os.path.join(HERE, "icons"), exist_ok=True)
    got = 0
    for icon in sorted(set(icons.values())):
        path = os.path.join(HERE, "icons", icon + ".webp")
        if os.path.exists(path):
            continue
        try:
            req = urllib.request.Request(ICON_URL + icon + ".webp", headers={"User-Agent": HEADERS["User-Agent"]})
            open(path, "wb").write(urllib.request.urlopen(req, timeout=30).read())
            got += 1
            log.info("Новая иконка: %s", icon)
        except Exception:
            log.exception("Не скачалась иконка %s", icon)
    log.info("Готово. Новых иконок: %d. Перезапусти оверлей, чтобы он взял новую базу.", got)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log.exception("Обновление не удалось — старая база не тронута")
        sys.exit(1)
