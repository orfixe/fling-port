"""Built-in "How to use" guide. Shown by `fling help`, the panel and the tray menu.

Button names here are the ones on screen. If a label changes in fling_app.py, change it here too.
"""

# Each language: list of [section title, body]. Body lines start with "• ".
HELP = {
 "ru": [
  [
   "Запуск и панель",
   "• Наберите `fling` в терминале или откройте «Fling» в меню.\n"
   "• Значок появится в трее, справа внизу.\n"
   "• Левый клик открывает панель, правый — меню.\n"
   "• Закрывается на Esc или кликом мимо.\n"
   "• «?» справа сверху — эта подсказка.\n"
   "• Рядом «Компактный вид». Остаются обложка и кнопки, подписи при наведении. Пульт прячется."
  ],
  [
   "Первое подключение",
   "• «Подключение ТВ…» — в панели, в меню значка или командой `fling setup`.\n"
   "• Нажмите «Найти и подключить всё».\n"
   "• У каждого шага свой значок: ✓ готово, ⏳ идёт, ! надо что-то нажать на ТВ, ✗ не вышло."
  ],
  [
   "Выбор телевизора",
   "• Внизу панели, рядом с именем ТВ, кнопка «Выбрать».\n"
   "• Из неё: «Найти ТВ заново», «Добавить ТВ по IP…», «Убрать добавленный вручную».\n"
   "• В окне «Подключение ТВ» то же самое называется «Выбрать ТВ…».\n"
   "• Если имена одинаковые, в меню пишется адрес. «пульт ✓» — пульт уже связан. «по IP» — этот ТВ добавили вручную."
  ],
  [
   "Связать с ТВ",
   "• Нажмите «Связать с ТВ». Если ТВ выключен — сначала «Включить ТВ».\n"
   "• На экране появится код из 6 знаков: цифры и буквы A–F.\n"
   "• Наберите его. Русская раскладка подходит. Или кнопки 0–9 и A–F под полем — они сами отправляют код на шестом знаке.\n"
   "• С клавиатуры нажмите Enter или «Связать»."
  ],
  [
   "Отправить видео на ТВ",
   "• «Отправить эту вкладку» отправляет открытую вкладку Firefox.\n"
   "• Из любой программы — Meta ⇧ C. Так написано на кнопке. Это Meta+Shift+C.\n"
   "• Ссылка уже скопирована — «Отправить скопированную ссылку».\n"
   "• Можно отправить YouTube, прямые файлы (mp4, mkv, mp3, m3u8…) и большинство видеосайтов.\n"
   "• Netflix и похожие так не уходят. Откройте их кнопками приложений, дальше пультом."
  ],
  [
   "Что играет сейчас",
   "• На панели обложка, название и время.\n"
   "• Пробел — пауза или снова играть.\n"
   "• ← и → — на 30 секунд назад или вперёд. Кнопки так и называются: «Назад 30 с» и «Вперёд 30 с».\n"
   "• Громкость — ползунок.\n"
   "• Стоп: «Остановить трансляцию» или Ctrl+.\n"
   "• ⟳ на обложке обновляет, что играет."
  ],
  [
   "Название и обложка с ТВ",
   "• Чтобы название и обложка были настоящие, нужен ADB. Это шаг «ADB: что играет, название и обложка» в «Подключение ТВ…».\n"
   "• На ТВ: Настройки → Система → О системе. Семь раз нажмите «Сборка ОС».\n"
   "• Потом Настройки → Система → Для разработчиков. Включите «Отладка по USB» и «Беспроводная отладка».\n"
   "• На вопрос на экране нажмите «Разрешить». Лучше с галочкой «Всегда».\n"
   "• Если ТВ показал код сопряжения, впишите порт и 6 цифр в том же шаге."
  ],
  [
   "YouTube на ТВ",
   "• Нажмите «Связать с YouTube на ТВ — видеть, что играет».\n"
   "• На ТВ: YouTube → Настройки → «Подключить телефон» или «Связать с ТВ по коду».\n"
   "• Введите 12 цифр с экрана.\n"
   "• Потом видно точное видео и время. Появляются «Предыдущее видео» и «Следующее видео».\n"
   "• Если ADB уже показывает, что играет, этот шаг можно пропустить."
  ],
  [
   "Пульт",
   "• В «НА ТЕЛЕВИЗОРЕ» — питание и приложения: YouTube, Netflix и остальные.\n"
   "• Стрелки, OK, «Назад», «Домой».\n"
   "• Колёсико или тачпад в любом месте панели двигают меню ТВ.\n"
   "• «Громк −», «Без звука», «Громк +» — звук телевизора.\n"
   "• Значок клавиатуры открывает строку для печати на ТВ.\n"
   "• Микрофон рядом — голосовой поиск. Нажмите ещё раз, когда закончите."
  ],
  [
   "Bluetooth-пульт",
   "• «Подключить как Bluetooth-пульт…». Если пульт по сети уже связан, это не нужно.\n"
   "• На ТВ: Настройки → Пульты и аксессуары → Добавить аксессуар → «Fling Remote».\n"
   "• KDE может спросить про сопряжение — подтвердите.\n"
   "• Окно сопряжения открыто 3 минуты.\n"
   "• По Bluetooth печатаются только латиница и цифры."
  ],
  [
   "Сделать снимок",
   "• «Сделать снимок» снимает экран телевизора.\n"
   "• Файл падает в «Изображения/Fling» и сразу открывается.\n"
   "• Чёрный кадр — видео защищено от снимков. Так у YouTube и Netflix. Меню и главный экран снимаются нормально.\n"
   "• Без ADB снимка не будет. Включите его в «Подключение ТВ…»."
  ],
  [
   "Экран ТВ, мышь и ADB-консоль",
   "• Всё это — в меню зелёного значка Android внизу панели (или в меню значка в трее → ADB). Нужен включённый ADB.\n"
   "• «Экран ТВ с мышью» открывает окно с картинкой ТВ. Щёлкните в нём — мышь станет курсором на ТВ. Alt возвращает мышь компьютеру.\n"
   "• Щелчок по обложке в панели открывает то же окно.\n"
   "• «Курсор на ТВ (без картинки)» — только курсор, без видео: маленькое окно, щёлкните в нём, мышь и клавиатура управляют ТВ.\n"
   "• Работает на scrcpy. Fling скачивает его сам при первом запуске (~12 МБ).\n"
   "• Видео YouTube и Netflix в окне может быть чёрным — это защита от записи, меню видно нормально.\n"
   "• «ADB-консоль…» — команды для ТВ. Пишите как в `adb shell`: `getprop`, `pm list packages`. `logcat`, `install`, `pull` — как есть. Есть «Быстрые команды», ↑/↓ — история, «Стоп» прерывает.\n"
   "• Команды, которые могут удалить данные или перезагрузить ТВ, Fling сначала переспросит."
  ],
  [
   "Команды и прочее",
   "• Язык — маленький флажок внизу справа.\n"
   "• «Запускать при входе» — чтобы Fling открывался сам.\n"
   "• Выход: «Выйти из Fling», Ctrl+Q или `fling quit`.\n"
   "• Команды:\n"
   "  - `fling` — открыть панель\n"
   "  - `fling setup` — подключение ТВ\n"
   "  - `fling cast-tab` — отправить вкладку\n"
   "  - `fling clip` — ссылка из буфера\n"
   "  - `fling screenshot` — сделать снимок\n"
   "  - `fling screen` / `fling mouse` — экран ТВ с мышью / курсор без картинки\n"
   "  - `fling adb` — ADB-консоль, `fling log` — журнал\n"
   "  - `fling send файл…` — на ТВ: APK ставится, видео и фото показываются, остальное копируется в Download. "
   "Так же работает перетаскивание файла на панель или ADB-консоль\n"
   "  - `fling help` — эта подсказка\n"
   "  - `fling quit` — выход\n"
   "  - `fling tui` — пульт в терминале"
  ],
  [
   "Если что-то не работает",
   "• ТВ не находится: оба должны быть в одной сети. Или «Выбрать» → «Добавить ТВ по IP…». Адрес — на ТВ, Настройки → Сеть.\n"
   "• Пауза на YouTube не слушается, пока не нажмёте «Связать с ТВ».\n"
   "• Старая обложка — включите ADB в «Подключение ТВ…».\n"
   "• Не получается ввести код — жмите кнопки под полем.\n"
   "• Чёрный снимок — защита видео, не поломка.\n"
   "• Журнал лежит в `~/.cache/fling/app.log`."
  ]
 ],
 "en": [
  [
   "Launch and panel",
   "• Type `fling` in a terminal, or open Fling from the menu.\n"
   "• The icon shows up in the tray, bottom right.\n"
   "• Left click opens the panel. Right click opens the menu.\n"
   "• Esc closes it. So does a click outside.\n"
   "• ? at the top right opens this guide.\n"
   "• Next to it, “Compact view”. Artwork and the keys stay, labels show on hover. The remote hides."
  ],
  [
   "First connection",
   "• Open “Connect TV…” from the panel, the tray menu, or `fling setup`.\n"
   "• Press “Find and connect everything”.\n"
   "• Each step has a mark: ✓ done, ⏳ working, ! needs you on the TV, ✗ failed."
  ],
  [
   "Select TV",
   "• At the bottom, next to the TV’s name, the button says “Select”.\n"
   "• That menu has “Search for TVs again”, “Add TV by IP…”, and “Remove the TV added by IP”.\n"
   "• In the Connect TV window the same choice is “Choose TV…”.\n"
   "• Two TVs with the same name: the menu shows the address. “remote ✓” means the remote is already paired. “by IP” means you added that TV yourself."
  ],
  [
   "Pair with TV",
   "• Click “Pair with TV”. If the TV is off, click “Turn on the TV” first.\n"
   "• A 6-character code shows up on the TV: digits and the letters A–F.\n"
   "• Type it. A Russian keyboard layout is fine. Or tap 0–9 and A–F under the field — those send it on the sixth character.\n"
   "• From the keyboard, press Enter or “Pair”."
  ],
  [
   "Cast video to TV",
   "• “Cast this tab” sends the open Firefox tab.\n"
   "• From any app, Meta ⇧ C. That’s what’s written on the button. Same keys as Meta+Shift+C.\n"
   "• Copied a link? “Cast copied link”.\n"
   "• YouTube works. So do direct files (mp4, mkv, mp3, m3u8…) and most video sites.\n"
   "• Netflix and the like won’t go this way. Open them with the app buttons, then use the remote."
  ],
  [
   "What is playing now",
   "• The panel shows the artwork, the title and the time.\n"
   "• Space pauses or plays.\n"
   "• ← and → jump 30 seconds. The buttons do the same.\n"
   "• Drag the volume slider.\n"
   "• Stop with “Stop casting”, or Ctrl+.\n"
   "• The ⟳ on the artwork refreshes what’s playing."
  ],
  [
   "Title and artwork",
   "• For the real title and artwork you need ADB. That’s the step “ADB: what's playing, title and artwork” in “Connect TV…”.\n"
   "• On the TV: Settings → System → About. Tap “Android TV OS build” seven times.\n"
   "• Then Settings → System → Developer options. Turn on “USB debugging” and “Wireless debugging”.\n"
   "• If the TV asks, choose “Allow”. Tick “Always” if it’s there.\n"
   "• If it shows a pairing code, type the port and the 6 digits in that same step."
  ],
  [
   "YouTube on TV",
   "• Click “Link YouTube on the TV — see what's playing”.\n"
   "• On the TV: YouTube → Settings → “Link with TV code”.\n"
   "• Type the 12 digits from the screen.\n"
   "• After that you get the exact video and time, plus “Previous video” and “Next video”.\n"
   "• Skip this if ADB already shows what’s playing."
  ],
  [
   "Remote",
   "• Under “ON THE TV”: power, and apps — YouTube, Netflix and the rest.\n"
   "• D-pad, OK, “Back”, “Home”.\n"
   "• Scroll anywhere on the panel to move around the TV. A touchpad works too.\n"
   "• “Vol −”, “Mute”, “Vol +” are the TV’s volume.\n"
   "• The keyboard icon opens a field for typing on the TV.\n"
   "• The mic next to it starts voice search. Click it again when you’re done."
  ],
  [
   "Bluetooth remote",
   "• “Connect as Bluetooth remote…”. Skip this if the network remote is already paired.\n"
   "• On the TV: Settings → Remotes & Accessories → Pair accessory → “Fling Remote”.\n"
   "• If KDE asks to confirm pairing, accept.\n"
   "• The pairing window stays open for 3 minutes.\n"
   "• Over Bluetooth you can only type Latin letters and digits."
  ],
  [
   "Take screenshot",
   "• “Take screenshot” grabs the TV screen.\n"
   "• The file lands in Pictures/Fling and opens right away.\n"
   "• A black picture means the video is copy-protected. YouTube and Netflix do that. Menus and the home screen come out fine.\n"
   "• No ADB, no screenshot. Turn it on in “Connect TV…”."
  ],
  [
   "TV screen, mouse and ADB console",
   "• All in the menu of the green Android icon at the bottom of the panel (or the tray icon menu → ADB). ADB has to be on.\n"
   "• “TV screen with mouse” opens a window with the TV picture. Click in it and the mouse becomes a pointer on the TV. Alt gives the mouse back.\n"
   "• A click on the artwork in the panel opens the same window.\n"
   "• “Cursor on the TV (no picture)” is just the pointer, no video: a small window, click in it, the mouse and keyboard drive the TV.\n"
   "• It runs on scrcpy. Fling downloads it by itself the first time (~12 MB).\n"
   "• YouTube and Netflix video may be black in the window — that's copy protection; menus show fine.\n"
   "• “ADB console…” runs commands on the TV. Type them as in `adb shell`: `getprop`, `pm list packages`. `logcat`, `install`, `pull` as they are. There are “Quick commands”, ↑/↓ for history, “Stop” ends a command.\n"
   "• Commands that may delete data or restart the TV ask first."
  ],
  [
   "Commands and the rest",
   "• Language is the small flag at the bottom right.\n"
   "• “Start at login” opens Fling when you log in.\n"
   "• Quit with “Quit Fling”, Ctrl+Q, or `fling quit`.\n"
   "• Commands:\n"
   "  - `fling` — open the panel\n"
   "  - `fling setup` — Connect TV\n"
   "  - `fling cast-tab` — cast the tab\n"
   "  - `fling clip` — link from the clipboard\n"
   "  - `fling screenshot` — take a screenshot\n"
   "  - `fling screen` / `fling mouse` — TV screen with mouse / cursor without the picture\n"
   "  - `fling adb` — ADB console, `fling log` — the log\n"
   "  - `fling send file…` — to the TV: APKs install, videos and pictures play, the rest is copied to Download. "
   "Dragging a file onto the panel or the ADB console does the same\n"
   "  - `fling help` — this guide\n"
   "  - `fling quit` — quit\n"
   "  - `fling tui` — remote in the terminal"
  ],
  [
   "If something doesn't work",
   "• TV not found: both have to be on the same network. Or “Select” → “Add TV by IP…”. The address is on the TV, Settings → Network.\n"
   "• YouTube ignores pause until you click “Pair with TV”.\n"
   "• Old artwork: turn on ADB in “Connect TV…”.\n"
   "• If the code won’t type, use the buttons under the field.\n"
   "• A black screenshot is copy protection, not a broken capture.\n"
   "• The log is `~/.cache/fling/app.log`."
  ]
 ]
}


# Windows replaces a section by its title. None drops the section (no Bluetooth remote on Windows).
WINDOWS = {
 "ru": {
  "Запуск и панель":
   "• Откройте «Fling» в меню Пуск. Или наберите `fling` в PowerShell или cmd.\n"
   "• Значок справа внизу, возле часов. Не видно — стрелка ^, перетащите его на панель задач.\n"
   "• Левый клик открывает панель, правый — меню.\n"
   "• Закрывается на Esc или кликом мимо.\n"
   "• «?» справа сверху — эта подсказка.\n"
   "• Рядом «Компактный вид». Остаются обложка и кнопки, подписи при наведении. Пульт прячется.",
  "Отправить видео на ТВ":
   "• Поставьте расширение Fling в браузер — Chrome, Edge, Firefox и похожие. Оно говорит Fling, какая вкладка открыта.\n"
   "• «Отправить эту вкладку» в панели, или кнопка Fling на панели браузера.\n"
   "• Из любой программы: Win+Shift+C. Если занято — Ctrl+Alt+Shift+C. В панели написано, какое сработало. В браузере: Alt+Shift+C.\n"
   "• Если видео на странице уже идёт, расширение продолжит его на ТВ с того же места.\n"
   "• Без расширения Fling берёт активную вкладку Firefox из файла сессии.\n"
   "• Скопированная ссылка — «Отправить скопированную ссылку».\n"
   "• Можно отправить YouTube, прямые файлы (mp4, mkv, mp3, m3u8…) и большинство видеосайтов.\n"
   "• Netflix и похожие так не уходят. Откройте их кнопками приложений, дальше пультом.",
  "Сделать снимок":
   "• «Сделать снимок» снимает экран телевизора.\n"
   "• Файл падает в `%USERPROFILE%\\Pictures\\Fling` — папка «Изображения», внутри Fling — и сразу открывается.\n"
   "• Чёрный кадр — видео защищено от снимков. Так у YouTube и Netflix. Меню и главный экран снимаются нормально.\n"
   "• Без ADB снимка не будет. Включите его в «Подключение ТВ…».",
  "Bluetooth-пульт": None,
  "Команды и прочее":
   "• Язык — маленький флажок внизу справа.\n"
   "• «Запускать при входе» — чтобы Fling открывался вместе с Windows.\n"
   "• Выход: «Выйти из Fling», Ctrl+Q или `fling quit`.\n"
   "• Команды (PowerShell или cmd):\n"
   "  - `fling` — открыть панель\n"
   "  - `fling setup` — подключение ТВ\n"
   "  - `fling cast-tab` — отправить вкладку\n"
   "  - `fling clip` — ссылка из буфера\n"
   "  - `fling screenshot` — сделать снимок\n"
   "  - `fling screen` / `fling mouse` — экран ТВ с мышью / курсор без картинки\n"
   "  - `fling adb` — ADB-консоль, `fling log` — журнал\n"
   "  - `fling send файл…` — на ТВ: APK ставится, видео и фото показываются, остальное копируется в Download. "
   "Так же работает перетаскивание файла на панель или ADB-консоль\n"
   "  - `fling help` — эта подсказка\n"
   "  - `fling quit` — выход",
  "Если что-то не работает":
   "• ТВ не находится: компьютер и ТВ должны быть в одной сети. При первом запуске всплывёт окно брандмауэра — разрешите Python или Fling в частной сети. Или «Выбрать» → «Добавить ТВ по IP…».\n"
   "• Пауза на YouTube не слушается, пока не нажмёте «Связать с ТВ».\n"
   "• Старая обложка — включите ADB в «Подключение ТВ…».\n"
   "• «Отправить эту вкладку» серая: расширение стоит, в его окошке зелёная точка, вкладка с видео открыта.\n"
   "• Микрофон молчит: Параметры → Конфиденциальность и защита → Микрофон → разрешите классическим приложениям.\n"
   "• Не получается ввести код — жмите кнопки под полем.\n"
   "• Чёрный снимок — защита видео, не поломка.\n"
   "• Журнал: `%LOCALAPPDATA%\\Fling\\logs\\app.log`.",
 },
 "en": {
  "Launch and panel":
   "• Open Fling from the Start menu. Or type `fling` in PowerShell or cmd.\n"
   "• The icon sits bottom right, by the clock. Hidden? Click the ^ arrow and drag it onto the taskbar.\n"
   "• Left click opens the panel. Right click opens the menu.\n"
   "• Esc closes it. So does a click outside.\n"
   "• ? at the top right opens this guide.\n"
   "• Next to it, “Compact view”. Artwork and the keys stay, labels show on hover. The remote hides.",
  "Cast video to TV":
   "• Add the Fling extension to your browser — Chrome, Edge, Firefox and the ones like them. It tells Fling which tab is open.\n"
   "• “Cast this tab” in the panel, or the Fling button in the browser toolbar.\n"
   "• From any app: Win+Shift+C. If something else owns it, Ctrl+Alt+Shift+C. The panel shows which one works. In the browser: Alt+Shift+C.\n"
   "• If the video on the page is already playing, the extension picks it up on the TV at the same spot.\n"
   "• No extension? Fling reads the active Firefox tab from its session file.\n"
   "• A copied link: “Cast copied link”.\n"
   "• YouTube works. So do direct files (mp4, mkv, mp3, m3u8…) and most video sites.\n"
   "• Netflix and the like won’t go this way. Open them with the app buttons, then use the remote.",
  "Take screenshot":
   "• “Take screenshot” grabs the TV screen.\n"
   "• The file lands in `%USERPROFILE%\\Pictures\\Fling` — your Pictures folder, inside Fling — and opens right away.\n"
   "• A black picture means the video is copy-protected. YouTube and Netflix do that. Menus and the home screen come out fine.\n"
   "• No ADB, no screenshot. Turn it on in “Connect TV…”.",
  "Bluetooth remote": None,
  "Commands and the rest":
   "• Language is the small flag at the bottom right.\n"
   "• “Start at login” opens Fling with Windows.\n"
   "• Quit with “Quit Fling”, Ctrl+Q, or `fling quit`.\n"
   "• Commands (PowerShell or cmd):\n"
   "  - `fling` — open the panel\n"
   "  - `fling setup` — Connect TV\n"
   "  - `fling cast-tab` — cast the tab\n"
   "  - `fling clip` — link from the clipboard\n"
   "  - `fling screenshot` — take a screenshot\n"
   "  - `fling screen` / `fling mouse` — TV screen with mouse / cursor without the picture\n"
   "  - `fling adb` — ADB console, `fling log` — the log\n"
   "  - `fling send file…` — to the TV: APKs install, videos and pictures play, the rest is copied to Download. "
   "Dragging a file onto the panel or the ADB console does the same\n"
   "  - `fling help` — this guide\n"
   "  - `fling quit` — quit",
  "If something doesn't work":
   "• TV not found: the PC and the TV have to be on the same network. On first start Windows Firewall pops up — allow Python or Fling on private networks. Or “Select” → “Add TV by IP…”.\n"
   "• YouTube ignores pause until you click “Pair with TV”.\n"
   "• Old artwork: turn on ADB in “Connect TV…”.\n"
   "• “Cast this tab” greyed out: the extension should be installed, its popup should show a green dot, and a video tab should be open.\n"
   "• Mic silent: Settings → Privacy & security → Microphone → allow desktop apps.\n"
   "• If the code won’t type, use the buttons under the field.\n"
   "• A black screenshot is copy protection, not a broken capture.\n"
   "• Log: `%LOCALAPPDATA%\\Fling\\logs\\app.log`.",
 },
}


def sections(lang: str, windows: bool = False):
    """The guide for one language, with the Windows variants applied."""
    out = []
    over = WINDOWS.get(lang, {}) if windows else {}
    for title, body in HELP[lang]:
        if title in over:
            if over[title] is None:
                continue
            body = over[title]
        out.append((title, body))
    return out
