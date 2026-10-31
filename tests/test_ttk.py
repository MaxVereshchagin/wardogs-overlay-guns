"""Сверка расчёта TTK (app/ballistics.py) с эталоном калькулятора MetaForge (tests/ttk_reference.txt).
Запуск: python\\python.exe -I tests\\test_ttk.py [путь к ballistics_v2.json]"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
import ballistics  # noqa: E402

path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "app", "metaforge_raw", "ballistics_v2.json")
b = json.load(open(path, encoding="utf-8"))
text = "".join(line for line in open(os.path.join(HERE, "ttk_reference.txt"), encoding="utf-8")
               if not line.startswith("#")).strip()
ok = bad = 0
for row in text.split(";"):
    name, rnd, level, hit, dist, shots, ms, dmg = row.split("|")
    r = ballistics.ttk(b, name, rnd, int(level), hit, int(dist))
    got = (r["shots"] or -1, round(r["ms"]) if r["ms"] is not None else -1, round(r["damage"], 2))
    want = (int(shots), int(ms), float(dmg))
    if got[0] == want[0] and abs(got[1] - want[1]) <= 1 and abs(got[2] - want[2]) <= 0.02:
        ok += 1
    else:
        bad += 1
        print(f"РАЗНИЦА {name} {rnd} ур.{level} {hit} {dist} м: у меня {got}, у MetaForge {want}")
print(f"Совпало {ok}, разница {bad} (данные баллистики от {b.get('generatedAt')})")
sys.exit(1 if bad else 0)
