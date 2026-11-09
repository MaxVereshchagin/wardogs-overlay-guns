# Wardogs Overlay — attachment stats, TTK, ammo and Gunsmith

**[English](#english)** · **[Русский](#русский)**

## English

An overlay for **Wardogs**. When you build a weapon at the vendor (**Equipment → ATTC.** or **AMMO**), a panel appears
next to the menu with the exact numbers the game doesn't show: which attachments are installed and what they do —
good in green, bad in red, ADS time in seconds — plus **TTK**: how many hits and seconds it takes to kill.
Works with the **English and Russian** game; the program itself speaks both (RU / EN).

### Download and run

1. Open **[Releases](../../releases)** and download `WardogsOverlay_v2.1.1.zip`.
2. Extract it (right click → «Extract All…»). Don't run it from inside the zip.
3. Run **`WardogsOverlay.exe`** (keep it together with the `_internal` folder). Nothing to install. The control panel opens and a **W** icon appears next to the clock.
4. In the game: screen mode **«Borderless windowed»**, then **Equipment Vendor → Equipment → ATTC.** or **AMMO**.

If Windows says «Windows protected your PC» — «More info» → «Run anyway» (the program is home-made and not signed).
Guide on Steam: [English](https://steamcommunity.com/sharedfiles/filedetails/?id=3831395119) ·
[Russian](https://steamcommunity.com/sharedfiles/filedetails/?id=3822235930).

### New in 2.1.1

- Fixed: on some PCs the control panel opened too narrow and cut off its right edge. The width is now calculated from the content.

### New in 2.1

- **Gunsmith suggests only what you have:** control panel → Gunsmith → «Use my levels» (Career, Assault, Medic, Recon, Support);
  «✕ don't have» under a suggestion — it suggests the next best. With levels on, one grey line shows the best locked one and where it unlocks.
- **No more junk in the temp folder:** the program is now a folder (exe + `_internal`), nothing is unpacked on start;
  leftovers of old versions (~200 MB each) are removed automatically.
- Control panel opens on start, can be resized (height is remembered); running the exe again just opens the panel.

### Features (2.0)

- **Attachments:** exact multipliers for every attachment and the total for the build.
- **TTK:** body and head, no armor and armor 1–4; fire rate, damage falloff and bullet travel included —
  matches the MetaForge calculator (checked on 376 cases). **Distance buttons** for weapons that lose damage with range.
- **AMMO tab:** magazine and ammo (FMJ / HP / AP), TTK of all three against every armor level; the fastest is green.
- **Gunsmith suggests** the best muzzle and grip (Balance / Recoil / ADS) with NOW and SUGGESTED columns.
- **Details:** the 4 main stats or every stat that changes. **Compare builds:** `Ctrl+Shift+S`.
- **Control panel** (double click the tray icon): status tiles, switches, hotkeys (a single F-key works too).
- **Panel for OBS:** Browser source → `http://127.0.0.1:8777/overlay.html` — same as the in-game panel.
- Tells you about new versions (checks the Steam guide, downloads nothing).

### Is it safe?

It only takes screenshots of the game window (process `WardogsClient`) and draws its own window — like OBS or Discord.
No memory reading, no injection, game files are not changed. Menu text is found by image matching, weapon names
by OCR (RapidOCR), attachments by their icons; stats and TTK come from MetaForge data.
A detailed log (`overlay.log`) is written next to the program.

What it does — and nothing more:
- finds the game window by asking Windows for each window's program name with `PROCESS_QUERY_LIMITED_INFORMATION`
  (this right cannot read or write another process's memory);
- listens only to its own hotkeys;
- goes online to only 2 places: the MetaForge database ("Update database") and the Steam guide (new version check);
- the OBS server listens on `127.0.0.1` only and is off by default.

It does **not** inject into the game, check for virtual machines, scan your files or send any data about you.

Why sandboxes (VirusTotal, ANY.RUN) show notes: the exe is a PyInstaller one-file build — it unpacks itself to a temp
folder and starts a second copy ("suspended process", "debugger check"); the "VMware / QEMU strings" sit inside
OpenCV's ffmpeg library (VMware video codec); "calls WMI" is Python asking for the Windows version.
[VirusTotal, v2.1](https://www.virustotal.com/gui/file/eca2e71176d18fd0f64ddd8b7d04d0938bd4b41c64ac0142fd0d5b750aa07c1b):
3 of 69 engines flag the exe, the rest are clean (Kaspersky, ESET, Microsoft, Dr.Web, Avast…); Arctic Wolf, SecureAge and Skyhigh
give heuristic verdicts ("behaves like…") for an unsigned PyInstaller program.
Download only from this repository's Releases or the Steam guide — there is an unrelated program with a similar name.

Requirements: Windows 10/11 64-bit.

---

## Русский

Оверлей для игры **Wardogs**. Когда собираешь пушку у продавца (**Экипировка → Модули** или **Боеприпасы**; в английской игре — **ATTC.** / **AMMO**),
сбоку появляется плашка: какая пушка, какие обвесы стоят и что они дают —
плюсы зелёным, минусы красным, время прицеливания в секундах, а ещё **TTK** — сколько попаданий
и секунд нужно, чтобы убить.

```
 GALIL                               ШТУРМОВАЯ
 Урон 28 · 650 в/мин · Дальн. 400 м · Прицел. 0.49 с
 МАГАЗИН   Galil 35 RND Magazine
 ПРИЦЕЛ    Spitfire 3X
 РУКОЯТЬ   AFG Angled Foregrip
 Отдача вбок                      −25%
 Разброс                          −22%
 Прицел. 0,49→0,46 с              −0,03 с
 TTK · 50 м       БЕЗ   БР.1  БР.2  БР.3  БР.4
 Тело              4×    6×    7×    8×   10×
 Голова            2×    3×    3×    4×    5×
```

### Скачать и запустить

1. Открой **[Releases](../../releases)** и скачай `WardogsOverlay_v2.1.1.zip`.
2. Распакуй (правой кнопкой → «Извлечь всё…»). Из самого архива не запускай.
3. Запусти **`WardogsOverlay.exe`** (рядом с ним должна лежать папка `_internal`). Ничего ставить не нужно. Откроется пульт, у часов появится значок **W**.
4. В игре: режим экрана **«Оконный без рамки»**, затем **Продавец → Экипировка → Модули** или **Боеприпасы**.

Если Windows покажет «Система Windows защитила ваш компьютер» — «Подробнее» → «Выполнить в любом случае»
(программа самодельная и не подписана).

### Новое в 2.1.1

- Исправлено: у некоторых пульт открывался узким и обрезал правый край. Ширина теперь считается по содержимому.

### Новое в 2.1

- **Оружейник советует только то, что у тебя есть:** пульт → «Оружейник» → «Учитывать мои уровни» (Карьера, Штурмовик, Медик,
  Разведчик, Поддержка); «✕ нет у меня» под советом — предложит следующий лучший. С уровнями одной серой строкой видно, какой лучший ещё закрыт и где откроется.
- **Больше не забивает диск:** программа теперь папка (exe + `_internal`), при запуске ничего не распаковывается во временную папку;
  мусор старых версий (~200 МБ на каждый запуск) удаляется сам.
- Пульт открывается при запуске, тянется по высоте (размер запоминается); повторный запуск exe просто открывает пульт.

### Что умеет (2.0)

- **Английский язык:** игра на английском распознаётся так же, как русская; сама программа говорит по-русски или по-английски.
- **Оружейник рекомендует:** лучшее дуло и рукоять для пушки (режимы Баланс / Отдача / ADS), колонки СЕЙЧАС и СОВЕТ.
- **Дистанция TTK:** кнопки дистанции у тех, кто теряет урон (ПП, пистолеты, дробовики, лук); у остальных — «урон держит до … м».
- **«Подробно»:** 4 главных стата или все меняющиеся.
- **Пульт** вместо окна настроек: плитки состояния, переключатели, клавиши (можно одну F-клавишу).
- **Обвесы:** точные множители по каждому обвесу и итог по сборке.
- **TTK:** в тело и в голову, без брони и против брони 1–4 (дистанция 50 м), с учётом скорострельности,
  падения урона и полёта пули. Расчёт совпадает с калькулятором MetaForge (сверено на 376 вариантах).
- **Вкладка «Боеприпасы»:** видит магазин и патроны (FMJ / HP / AP), сравнивает TTK всех трёх против каждой брони;
  самые быстрые — зелёным. TTK на «Модулях» считается с выбранными патронами.
- **Сравнение сборок:** `Ctrl+Shift+S` — запомнить сборку, дальше плашка показывает «было → сейчас».
- **Значок у часов:** правая кнопка — меню (поиск, обновить базу, настройки, журнал, выход),
  двойной щелчок — окно настроек с кнопкой «Инструкция».
- `Ctrl+Shift+O` — включить/выключить поиск прямо из игры. Без меню 10 секунд — поиск засыпает.
- Плашка для OBS: источник «Браузер» → `http://127.0.0.1:8777/overlay.html` (включается в пульте) — показывает то же, что плашка в игре.
- Сам сообщает о новой версии (смотрит руководство в Steam, ничего не скачивает).

### Как это работает

- Снимает **только окно игры** (по процессу `WardogsClient`), OBS и другие окна не мешают.
- Находит меню по надписям «МОДУЛИ» / ATTACHMENTS, «ШАГ 1» / STP 1 и «ЯЧЕЙКИ ДЛЯ ЭКИПИРОВКИ» / EQUIPMENT SLOTS в любом масштабе —
  подходит любое разрешение (720p…4K, 16:10, 21:9, 32:9) и игра в окне.
- Название пушки и патроны («30/30») читает распознаванием текста (RapidOCR),
  обвесы, магазины и коробки патронов узнаёт по иконкам, статы и TTK считает по данным MetaForge.
- **В игру не лезет**: не читает память, не меняет файлы — только смотрит на картинку, как OBS или Discord.
- Пишет подробный журнал (`overlay.log`) и понятный журнал событий (`события.txt`) рядом с программой.

### Безопасность

- Окно игры ищет так: спрашивает у Windows имя программы каждого окна с правом `PROCESS_QUERY_LIMITED_INFORMATION` —
  с ним нельзя ни прочитать, ни изменить память чужой программы.
- Слушает только свои клавиши. В интернет ходит только в 2 места: база MetaForge («Обновить базу») и руководство Steam (проверка версии).
- Сервер для OBS — только `127.0.0.1`, по умолчанию выключен.
- **Не** инжектится в игру, **не** проверяет виртуальную машину, **не** сканирует файлы и **не** отправляет данные о тебе.

Почему песочницы (VirusTotal, ANY.RUN) пишут пометки: exe собран PyInstaller'ом (до 2.1 — в один файл, распаковывался во временную
папку и запускал вторую копию — «приостановленный процесс», «проверка отладчика»); «строки VMware / QEMU» лежат внутри
видеобиблиотеки ffmpeg из OpenCV (видеокодек VMware); «вызывает WMI» — так Python узнаёт версию Windows.
[VirusTotal, версия 2.1](https://www.virustotal.com/gui/file/eca2e71176d18fd0f64ddd8b7d04d0938bd4b41c64ac0142fd0d5b750aa07c1b):
3 из 69 антивирусов отмечают exe, остальные чисто (Kaspersky, ESET, Microsoft, Dr.Web, Avast…); Arctic Wolf, SecureAge и Skyhigh
ругаются эвристикой («похоже на…») на программу без подписи, собранную PyInstaller'ом.
Скачивай только из Releases этого репозитория или по ссылке из руководства Steam — есть другая программа с похожим названием.

Требования: Windows 10/11 64 бит, игра на **русском или английском** языке.

## Для разработки

```
app/wardogs_overlay.py   — сама программа (распознавание, окно поверх игры, настройки)
app/ballistics.py        — расчёт TTK (как калькулятор MetaForge)
app/tray.py              — значок у часов с меню
app/update_database.py   — скачивание базы и баллистики с MetaForge
app/server.py, overlay.html — плашка для OBS
tests/run_tests.py       — прогон по ~1300 вариантам картинки (русская и английская игра, разрешения, сжатие, курсор, ловушки)
tests/test_ttk.py        — сверка TTK с эталоном MetaForge (tests/ttk_reference.txt)
tests/test_ammo.py       — проверка вкладки «Боеприпасы»
tests/test_gunsmith.py, test_ttk_dist.py, test_english.py, test_pult.py, test_obs.py — оружейник, дистанции, английский, пульт, OBS
tests/test_smith_levels.py, test_clean_mei.py, test_pult_size.py, test_en_21.py — 2.1: уровни и «нет у меня», чистка _MEI, размер пульта, английский
tests/test_pult_width.py — 2.1.1: пульт не обрезается справа (и с крупными шрифтами)
```

Запуск из кода: Python 3.12 с `numpy`, `opencv-python-headless`, `mss`, `rapidocr-onnxruntime`;
`python app/wardogs_overlay.py --tray` (или `--menu` — консольное меню).
Сборка с 2.1 — PyInstaller папкой (onedir: exe + `_internal`, `--windowed`; данные `ref_*.png` и `ref_*_en.png`, `overlay.html`, `overlay.ico`, инструкции).

## Данные

Статы, множители, баллистика и иконки — с [MetaForge](https://metaforge.app/wardogs/database), иконки — из игры Wardogs.
В репозитории их нет: программа скачивает их у каждого пользователя сама.
Проект не связан с разработчиками Wardogs и с MetaForge.
