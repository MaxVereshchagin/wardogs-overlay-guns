"""TTK: сколько попаданий и сколько времени нужно, чтобы убить (данные баллистики MetaForge из файлов игры).

Урон за попадание = урон патрона (× дробинок) × множитель части тела (по классу оружия)
                    × падение урона с дистанцией × потеря скорости пули × правила патрона (FMJ / HP / AP).
Броня (бронежилет или шлем, если закрывает эту часть тела): срезает reduction % × пробитие патрона,
пока у пластины есть прочность; каждое попадание её изнашивает, сломалась — дальше урон полный.
Время = (попаданий − 1) × интервал между выстрелами + время полёта пули.
Сверено с калькулятором metaforge.app/wardogs/ballistics (tests/ttk_reference.txt).
"""
import math

ROUNDS_RU = {"FMJ": "обычные", "HollowPoint": "экспансивные (HP)", "ArmorPiercing": "бронебойные (AP)"}
MAX_M = 2000           # полёт пули считаем до 2 км, по метру
AIR = 1.225            # плотность воздуха

_flight_cache = {}


def _rules_mult(rules, rnd, tier, hit):
    """Произведение (1 + pct/100) по правилам, которые подходят к патрону, броне и части тела."""
    m = 1.0
    for r in rules or []:
        if r.get("rounds") and rnd not in r["rounds"]:
            continue
        if r.get("targets") and (tier == "none" or tier not in r["targets"]):
            continue
        if r.get("zones") and hit not in r["zones"]:
            continue
        m *= 1 + r["pct"] / 100
    return m


def _falloff(points, dist):
    """Падение урона с дистанцией: точки [метры, доля, наклон справа, наклон слева, плавно?]."""
    if not points:
        return 1.0
    if dist <= points[0][0]:
        return points[0][1]
    for i in range(1, len(points)):
        x0, y0, out0, _, smooth = points[i - 1][:5]
        x1, y1, _, in1 = points[i][:4]
        if dist > x1:
            continue
        span = x1 - x0
        if span == 0:
            return y1
        t = (dist - x0) / span
        if not smooth:
            return y0 + (y1 - y0) * t
        t2, t3 = t * t, t * t * t      # плавная кривая (кубическая, по наклонам на концах)
        return ((2 * t3 - 3 * t2 + 1) * y0 + (t3 - 2 * t2 + t) * out0 * span
                + (-2 * t3 + 3 * t2) * y1 + (t3 - t2) * in1 * span)
    return points[-1][1]


def _flight(b, w, cal_key, cal):
    """Время полёта (мс) и скорость пули на каждом метре до 2 км. None — нет данных о скорости."""
    key = (b.get("generatedAt"), w["name"], cal_key)
    if key in _flight_cache:
        return _flight_cache[key]
    v0 = w.get("muzzleVelocity")
    step = None
    if v0:
        if cal.get("dragModel"):
            curve = (b.get("dragCurves") or {}).get(cal["dragModel"])
            if curve and cal.get("mass") and cal.get("diameter"):
                def cd(v):
                    if v <= curve[0][0]:
                        return curve[0][1]
                    for (va, ca), (vb, cb) in zip(curve, curve[1:]):
                        if v <= vb:
                            return ca + (v - va) / (vb - va) * (cb - ca)
                    return curve[-1][1]
                k = 0.5 * AIR * math.pi * (cal["diameter"] / 200) ** 2 * cal.get("dragCoefficient", 1) / cal["mass"]
                step = lambda v: max(v - k * cd(v) * v, 10)   # noqa: E731
        else:
            decay = math.exp(-(cal.get("speedDecay") or 0))
            step = lambda v: v * decay   # noqa: E731
    table = None
    if step:
        times, speeds = [0.0], [float(v0)]
        v, t = float(v0), 0.0
        for _ in range(MAX_M):
            t += 1000 / v
            v = step(v)
            times.append(t)
            speeds.append(v)
        table = (times, speeds)
    _flight_cache[key] = table
    return table


def _at(arr, dist):
    d = min(max(dist, 0), MAX_M)
    i = int(d)
    return arr[i] + (d - i) * (arr[min(i + 1, MAX_M)] - arr[i])


def weapon(b, name):
    return next((w for w in b.get("weapons", []) if w.get("name") == name), None)


def armour_for(b, level, hit):
    """Броня уровня level (бронежилет и шлем), которая закрывает эту часть тела; 0 — без брони."""
    if not level:
        return None
    tiers = {"Light": 1, "Medium": 2, "Heavy": 3, "SuperHeavy": 4}
    for slot in ("armor", "helmet"):
        a = next((x for x in b.get("armour", []) if x.get("slot") == slot
                  and tiers.get(x["tier"].split(".")[-1]) == level), None)
        if a and hit in a.get("covers", []):
            return a
    return None


def loads(b, w):
    cal = (b.get("calibres") or {}).get(w.get("calibre") or "")
    return (cal or {}).get("loads") or ["FMJ"]


def ttk(b, w, rnd="FMJ", level=0, hit="torso_upper", dist=50):
    """→ {"shots", "ms", "damage", "round"} или None (нет данных). shots = None — не убить (больше 999)."""
    if isinstance(w, str):
        w = weapon(b, w)
    if not w:
        return None
    cal_key = w.get("calibre")
    cal = (b.get("calibres") or {}).get(cal_key or "")
    zone = (b.get("hitboxes") or {}).get(w.get("weaponClass") or "", {}).get(hit)
    if not cal or zone is None or not w.get("roundsPerMinute"):
        return None
    if cal.get("loads") and rnd not in cal["loads"]:
        rnd = "FMJ"
    health = b.get("assumedHealth", 100)
    wrules = (w.get("rules") or {}).get(cal_key, [])
    fl = _flight(b, w, cal_key, cal)
    fall = _falloff(w.get("falloff"), dist)
    typical = w.get("typicalSpeed") or cal.get("typicalSpeed")
    if typical and w.get("weaponClass") and fl:
        fall *= min(1.0, _at(fl[1], dist) / typical)
    flight_ms = _at(fl[0], dist) if fl and dist > 0 else 0.0
    base = cal["baseDamage"] * (cal.get("pellets") or 1)
    raw = base * zone * fall

    def rules(tier):
        return _rules_mult(cal.get("rules"), rnd, tier, hit) * _rules_mult(wrules, rnd, tier, hit)

    def when(n):
        return (n - 1) * 60000 / w["roundsPerMinute"] + flight_ms

    bare = max(0.0, raw * rules("none"))
    arm = armour_for(b, level, hit)
    if not arm:
        if bare <= 0:
            return {"shots": None, "ms": None, "damage": 0.0, "round": rnd}
        n = math.ceil(health / bare)
        return {"shots": n, "ms": when(n), "damage": bare, "round": rnd}
    cut = min(100.0, arm["reduction"] * (cal.get("pen") or {}).get(rnd, 1))
    hit_dmg = max(0.0, raw * rules(arm["tier"]) * (1 - cut / 100))
    plate_rules = [r for r in (cal.get("plate") or []) + ((w.get("plate") or {}).get(cal_key) or [])
                   if r.get("slot") == arm["slot"]]
    wear = base * fall * _rules_mult(plate_rules, rnd, arm["tier"], "")
    left, plate, n = health, arm.get("durability", math.inf), 0
    while left > 0 and n < 999:
        n += 1
        if plate > 0:
            left -= hit_dmg
            plate -= wear
        else:
            left -= bare
    if left > 0:
        return {"shots": None, "ms": None, "damage": hit_dmg, "round": rnd}
    return {"shots": n, "ms": when(n), "damage": hit_dmg, "round": rnd}
