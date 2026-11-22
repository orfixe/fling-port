"""Built-in "How to use" guide for Fling on Linux (shown by `fling help`, the panel and the tray).
Drafted by a local LLM (gpt-oss-20b) from a fact list, then checked and corrected by hand."""

# Each language: list of (section title, body). Body lines start with "• ".
HELP = {
 "ru": [
  [
   "Запуск и панель",
   "• Введите команду `fling` в терминале или выберите пункт меню «Fling».\n• Значок Fling появится в трее (справа внизу).\n• Левый клик по значку откроет панель, правый клик — покажет меню.\n• Панель можно закрыть клавишей Esc или щелчком вне панели."
  ],
  [
   "Первое подключение",
   "• Откройте строку «Подключение ТВ…» в панели (или в меню значка в трее, или командой `fling setup`).\n• Нажмите «Найти и подключить всё».\n• Следите за значками шагов: ✓ готово, ⏳ идёт, ! нужно действие на ТВ, ✗ ошибка."
  ],
  [
   "Выбор телевизора",
   "• Нажмите кнопку «ТВ…» (или «Сменить ТВ») в правом нижнем углу панели.\n• Выберите нужный телевизор из списка.\n• При необходимости нажмите «Найти ТВ заново», «Добавить ТВ по IP…» или «Убрать добавленный вручную».\n• Обратите внимание на отметки «пульт ✓» и «по IP» – одинаковые имена могут отличаться адресом."
  ],
  [
   "Связать пульт",
   "• Выберите пункт «Связать пульт с ТВ…».\n• На телевизоре появится код из 6 символов (цифры и буквы A–F).\n• Введите код с клавиатуры (русская раскладка тоже подходит) или нажмите кнопки 0–9 и A–F под полем.\n• После шестого символа код отправляется автоматически."
  ],
  [
   "Отправить видео на ТВ",
   "• Нажмите кнопку «Отправить эту вкладку», чтобы передать текущую вкладку Firefox на ТВ.\n• То же действие можно выполнить в любой программе сочетанием клавиш Meta+Shift+C.\n• Для отправки ссылки из буфера нажмите «Отправить скопированную ссылку».\n• Поддерживаются YouTube, прямые файлы (mp4, mkv, mp3, m3u8…) и большинство видеосайтов."
  ],
  [
   "Что играет сейчас",
   "• На панели отображается обложка, название и время воспроизведения.\n• Нажмите пробел для паузы или воспроизведения.\n• Кнопки ← и → перемещают видео на 30 секунд назад/вперёд.\n• Ползунок громкости регулирует уровень звука.\n• Ctrl+. остановит трансляцию («Остановить трансляцию»).\n• Нажмите кнопку ⟳ на обложке, чтобы обновить информацию."
  ],
  [
   "Название и обложка с ТВ",
   "• Для получения точного названия и обложки включите отладку по сети через ADB.\n• На телевизоре откройте Настройки → Система → О системе → нажмите «Сборка» 7 раз.\n• В разделе Для разработчиков включите отладку по сети.\n• При запросе «Разрешить отладку?» выберите «Разрешить» (лучше «Всегда»)."
  ],
  [
   "YouTube на ТВ",
   "• Нажмите кнопку «Связать с YouTube на ТВ».\n• На телевизоре откройте приложение YouTube и перейдите в Настройки.\n• Выберите пункт «Связать с ТВ по коду» и введите 12‑значный код.\n• После связывания отображаются точное видео, время и кнопки «Предыдущее видео», «Следующее видео»."
  ],
  [
   "Пульт",
   "• В разделе «НА ТЕЛЕВИЗОРЕ» доступны кнопки питания и приложений (YouTube, Netflix и др.).\n• Крестовина с OK, кнопки «Назад» и «Домой».\n• Колёсико мыши или тачпад над панелью позволяют перемещаться по меню ТВ.\n• Кнопки громкости управляют звуком.\n• Поле «Напечатать на ТВ» позволяет вводить текст на телевизоре.\n• Микрофон запускает голосовой поиск."
  ],
  [
   "Bluetooth-пульт",
   "• Выберите пункт «Подключить как Bluetooth‑пульт…».\n• На телевизоре откройте Настройки → Пульты и аксессуары → Добавить аксессуар → выберите «Fling Remote».\n• Подтвердите подключение, если KDE попросит.\n• Окно сопряжения будет открыто 3 минуты; при соединении можно печатать только латиницу."
  ],
  [
   "Снимок экрана ТВ",
   "• Нажмите кнопку «Снимок экрана ТВ».\n• Скриншот сохраняется в папку Изображения/Fling и открывается сразу.\n• Если снимок чёрный, значит видео защищено DRM (YouTube/Netflix).\n• Для корректного снимка включите отладку ADB."
  ],
  [
   "Команды и прочее",
   "• Язык переключается флажками 🇬🇧 / 🇷🇺 в правом верхнем углу панели; «?» рядом с ними открывает эту инструкцию.\n• Включите запуск при входе через пункт «Запускать при входе» (Start at login).\n• Для выхода из Fling нажмите Ctrl+Q или используйте команду `fling quit`.\n• Команды терминала:\n  - `fling` – открыть панель\n  - `fling setup` – подключение ТВ\n  - `fling cast-tab` – отправить вкладку\n  - `fling clip` – ссылка из буфера\n  - `fling screenshot` – снимок ТВ\n  - `fling help` – эта инструкция\n  - `fling quit` – выход\n  - `fling tui` – пульт в терминале"
  ],
  [
   "Если что-то не работает",
   "• Если ТВ не находится, убедитесь, что обе системы подключены к одной сети Wi‑Fi или добавьте ТВ по IP через «Добавить ТВ по IP…».\n• Пауза на YouTube может не работать без связанного пульта. Свяжите пульт с помощью «Связать пульт с ТВ…».\n• Если старые обложки отображаются, включите отладку ADB.\n• При невозможности ввода кода попробуйте нажать кнопки под полем.\n• Чёрный снимок экрана – DRM‑защищённый видеофайл.\n• Журнал работы хранится в `~/.cache/fling/app.log`."
  ]
 ],
 "en": [
  [
   "Launch and Panel",
   "• Enter the command `fling` in the terminal or select the menu item \"Fling\".\n• The Fling icon will appear in the tray (bottom right).\n• Left-click on the icon opens the panel, right-click shows the menu.\n• The panel can be closed with Esc or by clicking outside it."
  ],
  [
   "First connection",
   "• Open “Connect TV…” in the panel (or in the tray icon's menu, or run `fling setup`).\n• Press “Find and connect everything”.\n• Watch the step marks: ✓ done, ⏳ working, ! needs you on the TV, ✗ failed."
  ],
  [
   "Select TV",
   "• Press the button \"TV…\" (or \"Switch TV\") in the lower right corner of the panel.\n• Choose the desired TV from the list.\n• If needed, press \"Find TVs again\", \"Add TV by IP…\" or \"Remove a manually added TV\".\n• Note the marks \"remote ✓\" and \"by IP\" – identical names may differ by address."
  ],
  [
   "Connect remote",
   "• Select the item \"Pair TV remote…\".\n• A code of 6 symbols (digits and letters A–F) will appear on the TV.\n• Enter the code with the keyboard (Russian layout also works) or press the buttons 0–9 and A–F under the field.\n• After the sixth symbol the code is sent automatically."
  ],
  [
   "Cast video to TV",
   "• Click \"Cast this tab\" to send the current Firefox tab to the TV.\n• The same action can be performed in any program with Meta+Shift+C.\n• To cast a clipboard URL click \"Cast copied link\".\n• Supports YouTube, direct files (mp4, mkv, mp3, m3u8…) and most video sites."
  ],
  [
   "What is playing now",
   "• The panel displays the cover, title and playback time.\n• Press space to pause or play.\n• Buttons ← and → move the video 30 seconds back/forward.\n• The volume slider adjusts the sound level.\n• Ctrl+. stops casting (\"Stop casting\").\n• Click the ⟳ button on the cover to refresh information."
  ],
  [
   "Title and cover from TV",
   "• To obtain an accurate title and cover enable network debugging via ADB.\n• On the TV open Settings → System → About → tap \"Build\" 7 times.\n• In the Developer options enable network debugging.\n• When prompted with \"Allow debugging?\", choose \"Allow\" (preferably \"Always\")."
  ],
  [
   "YouTube on TV",
   "• Click \"Link YouTube on the TV\".\n• On the TV open the YouTube app and go to Settings.\n• Select \"Link with TV by code\" and enter the 12‑digit code.\n• After linking, the exact video, time and buttons \"Previous video\", \"Next video\" appear."
  ],
  [
   "Remote control",
   "• In the section \"ON THE TV\" you can use power and app buttons (YouTube, Netflix, etc.).\n• The cross with OK, the \"Back\" and \"Home\" buttons.\n• The mouse wheel or touchpad above the panel allows navigation through the TV menu.\n• Volume buttons control sound.\n• The field \"Type on TV\" lets you enter text on the TV.\n• The microphone starts voice search."
  ],
  [
   "Bluetooth remote",
   "• Select \"Connect as Bluetooth remote…\".\n• On the TV open Settings → Pairs and accessories → Add accessory → choose \"Fling Remote\".\n• Confirm the connection if KDE asks.\n• The pairing window stays open for 3 minutes; only Latin letters can be typed via Bluetooth."
  ],
  [
   "TV screenshot",
   "• Click \"TV screenshot\".\n• The screenshot is saved in Pictures/Fling and opens immediately.\n• If the screenshot is black, the video is DRM‑protected (YouTube/Netflix).\n• For a correct screenshot enable ADB debugging."
  ],
  [
   "Commands and other",
   "• Switch the language with the 🇬🇧 / 🇷🇺 flags in the panel's top-right corner; the ? next to them opens this guide.\n• Enable startup at login via the option \"Start at login\".\n• To exit Fling press Ctrl+Q or use the command `fling quit`.\n• Terminal commands:\n  - `fling` – open panel\n  - `fling setup` – connect TV\n  - `fling cast-tab` – cast tab\n  - `fling clip` – clipboard URL\n  - `fling screenshot` – TV screenshot\n  - `fling help` – this instruction\n  - `fling quit` – exit\n  - `fling tui` – remote in terminal"
  ],
  [
   "If something doesn't work",
   "• If the TV is not found, ensure both systems are on the same Wi‑Fi network or add a TV by IP via \"Add TV by IP…\".\n• Pause may not work on YouTube without a linked remote. Link the remote using \"Pair TV remote…\".\n• If old covers appear, enable ADB‑debugging.\n• If you cannot enter the code, try pressing the buttons under the field.\n• A black screenshot indicates DRM‑protected video.\n• The log is stored in `~/.cache/fling/app.log`."
  ]
 ]
}


# ---------------------------------------------------------------- Windows: sections that differ from Linux
# Replaces a section body by its title; None drops the section (no Bluetooth remote on Windows).
WINDOWS = {
 "ru": {
  "Запуск и панель":
   "• Откройте «Fling» в меню Пуск (или введите `fling` в PowerShell / cmd).\n• Значок Fling появится в области уведомлений справа внизу (если не видно — нажмите стрелку ^ и перетащите его на панель задач).\n• Левый клик по значку откроет панель, правый клик — покажет меню.\n• Панель закрывается клавишей Esc или щелчком вне панели.",
  "Отправить видео на ТВ":
   "• Поставьте расширение Fling в браузер (Chrome, Edge, Firefox) — оно сообщает Fling, какая вкладка открыта.\n• Нажмите «Отправить эту вкладку» в панели — или кнопку Fling на панели браузера.\n• Сочетание клавиш в любой программе: Win+Shift+C (если оно занято — Ctrl+Alt+Shift+C; в панели показано, какое работает). В браузере: Alt+Shift+C.\n• Если видео на странице уже идёт, расширение продолжит его на ТВ с того же места.\n• Без расширения Fling читает активную вкладку Firefox из файла сессии.\n• «Отправить скопированную ссылку» — для скопированной ссылки.",
  "Bluetooth-пульт": None,
  "Команды и прочее":
   "• Язык переключается флажками 🇬🇧 / 🇷🇺 в правом верхнем углу панели; «?» рядом с ними открывает эту инструкцию.\n• Автозапуск — пункт «Запускать при входе».\n• Выход — Ctrl+Q или команда `fling quit`.\n• Команды (PowerShell / cmd):\n  - `fling` – открыть панель\n  - `fling setup` – подключение ТВ\n  - `fling cast-tab` – отправить вкладку\n  - `fling clip` – ссылка из буфера\n  - `fling screenshot` – снимок ТВ\n  - `fling help` – эта инструкция\n  - `fling quit` – выход",
  "Если что-то не работает":
   "• ТВ не находится: компьютер и ТВ должны быть в одной сети; при первом запуске разрешите Python/Fling доступ к частной сети в окне брандмауэра Windows. Можно добавить ТВ по IP.\n• Пауза на YouTube может не работать без связанного пульта — «Связать пульт с ТВ…».\n• Старые обложки — подключите ADB.\n• Кнопка «Отправить эту вкладку» серая — проверьте, что расширение установлено и в его окне горит зелёная точка.\n• Микрофон молчит — Параметры → Конфиденциальность → Микрофон → разрешите классическим приложениям.\n• Журнал: `%LOCALAPPDATA%\\Fling\\logs\\app.log`.",
 },
 "en": {
  "Launch and Panel":
   "• Open “Fling” from the Start menu (or type `fling` in PowerShell / cmd).\n• The Fling icon appears in the notification area, bottom right (if hidden, click the ^ arrow and drag it onto the taskbar).\n• Left-click the icon for the panel, right-click for the menu.\n• Close the panel with Esc or by clicking outside it.",
  "Cast video to TV":
   "• Add the Fling extension to your browser (Chrome, Edge, Firefox) — it tells Fling which tab is open.\n• Click “Cast this tab” in the panel — or the Fling button in the browser toolbar.\n• Shortcut in any app: Win+Shift+C (if taken, Ctrl+Alt+Shift+C; the panel shows which one works). In the browser: Alt+Shift+C.\n• If the page's video is already playing, the extension resumes it on the TV at the same spot.\n• Without the extension Fling reads the active Firefox tab from its session file.\n• “Cast copied link” casts a copied link.",
  "Bluetooth remote": None,
  "Commands and other":
   "• Switch the language with the 🇬🇧 / 🇷🇺 flags in the panel's top-right corner; the ? next to them opens this guide.\n• Start with Windows: “Start at login”.\n• Quit: Ctrl+Q or `fling quit`.\n• Commands (PowerShell / cmd):\n  - `fling` – open panel\n  - `fling setup` – connect TV\n  - `fling cast-tab` – cast tab\n  - `fling clip` – clipboard URL\n  - `fling screenshot` – TV screenshot\n  - `fling help` – this guide\n  - `fling quit` – exit",
  "If something doesn't work":
   "• TV not found: the PC and the TV must be on the same network; on first start allow Python/Fling on private networks in the Windows Firewall prompt. You can also add a TV by IP.\n• Pause may not work on YouTube without a paired remote — “Pair TV remote…”.\n• Old artwork — connect ADB.\n• “Cast this tab” greyed out — check the extension is installed and its popup shows a green dot.\n• Mic silent — Settings → Privacy → Microphone → allow desktop apps.\n• Log: `%LOCALAPPDATA%\\Fling\\logs\\app.log`.",
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
