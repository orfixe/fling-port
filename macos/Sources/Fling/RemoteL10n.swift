// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation

/// Russian for the remote extras (modes, favourite apps, scenes, sleep timer) — the same
/// phrases as desktop/fling_remote.py. Looked up by `tr()` after the main table.
enum RemoteRU {
    static let exact: [String: String] = [
        // modes
        "Arrows": "Стрелки", "Touchpad": "Тачпад", "Numbers": "Цифры", "Media": "Медиа",
        "Remote: arrows, touchpad, number keys or media keys": "Пульт: стрелки, тачпад, цифры или медиаклавиши",
        "Drag on the pad for arrows\nClick for OK, right-click for Back":
            "Ведите по панели — стрелки\nЩелчок — OK, правая кнопка — Назад",
        "Touchpad: drag for arrows, click for OK, right-click for Back, middle-click for Home":
            "Тачпад: ведите — стрелки, щелчок — OK, правая кнопка — Назад, средняя — Домой",
        "Channel +": "Канал +", "Channel −": "Канал −", "CH": "CH",
        "Input": "Источник", "Input source (HDMI, antenna…)": "Источник сигнала (HDMI, антенна…)",
        "Guide": "Телегид", "TV guide": "Телепрограмма",
        "Info": "Инфо", "Programme info": "Сведения о передаче",
        "Settings": "Настройки", "TV settings": "Настройки ТВ",
        "Previous track": "Предыдущий трек", "Next track": "Следующий трек",
        "Rewind": "Перемотка назад", "Fast-forward": "Перемотка вперёд",
        "Play / Pause": "Пауза / Играть", "Stop": "Стоп",
        "Subtitles": "Субтитры", "Audio track": "Звуковая дорожка",
        "Red button": "Красная кнопка", "Green button": "Зелёная кнопка",
        "Yellow button": "Жёлтая кнопка", "Blue button": "Синяя кнопка",
        "Colour buttons — for TV channels and teletext": "Цветные кнопки — для телеканалов и телетекста",
        // favourite apps
        "Click to open on the TV, right-click to edit": "Щелчок — открыть на ТВ, правая кнопка — изменить",
        "Move left": "Сдвинуть влево", "Move right": "Сдвинуть вправо",
        "Rename…": "Переименовать…", "Remove": "Убрать",
        "Add an app": "Добавить приложение",
        "Open on the TV: ": "Открытое на ТВ: ",
        "Enter a package…": "Указать пакет…",
        "Restore the default set": "Вернуть стандартный набор",
        "No more room — remove an app first.": "Мест больше нет — уберите какое-нибудь приложение.",
        "App package, e.g. org.videolan.vlc": "Пакет приложения, например org.videolan.vlc",
        "Button label": "Название на кнопке",
        "Add": "Добавить", "Save": "Сохранить",
        "That doesn't look like an Android package name.": "Это не похоже на имя пакета Android.",
        "Apps open once the remote is paired with the TV.": "Открыть приложение можно, когда пульт связан с ТВ.",
        // more menu, power, sleep timer
        "More: scenes, sleep timer, power": "Ещё: сцены, таймер сна, включение",
        "Turn on the TV": "Включить ТВ", "Turn off the TV": "Выключить ТВ",
        "Sleep timer": "Таймер сна", "Cancel the timer": "Отменить таймер",
        "The TV turns off in ": "ТВ выключится через ",
        "Sleep timer cancelled: another TV was selected.": "Таймер сна отменён: выбран другой ТВ.",
        "Can't turn the TV off: the remote isn't paired with it.": "Не могу выключить ТВ: пульт не связан с ним.",
        // scenes
        "Scenes": "Сцены", "No scenes yet": "Сцен пока нет", "New scene…": "Новая сцена…", "Edit": "Изменить",
        "Stop the scene: ": "Остановить сцену: ",
        "Movie night": "Вечер кино", "Fling — scene": "Fling — сцена", "Scene": "Сцена",
        "A scene runs its steps in order: for example, it turns on the TV, opens an app and sets the volume. Run it from the “⋯” menu in the panel or from the menu-bar icon's right-click menu.":
            "Сцена выполняет шаги по порядку: например, включает ТВ, открывает приложение и ставит громкость. Запустить её можно из меню «⋯» на панели или из меню значка в строке меню (правый щелчок).",
        "Name": "Название", "Switch to this TV first: ": "Сначала переключиться на ТВ: ",
        "Add a step": "Добавить шаг", "Up": "Выше", "Down": "Ниже", "Delete step": "Удалить шаг",
        "Delete scene": "Удалить сцену", "Run": "Запустить",
        "Delete the scene “": "Удалить сцену «", "”?": "»?",
        "Open an app": "Открыть приложение", "Remote button": "Кнопка пульта", "Wait": "Подождать",
        "Open: ": "Открыть: ", "TV volume: ": "Громкость ТВ: ", "Button: ": "Кнопка: ",
        "Wait: ": "Подождать: ", "Sleep timer: ": "Таймер сна: ",
        "Give the scene a name.": "Дайте сцене название.", "Add at least one step.": "Добавьте хотя бы один шаг.",
    ]

    static let patterns: [(NSRegularExpression, String)] = [
        (#"^(\d+) min$"#, "$1 мин"),
        (#"^(\d+) s$"#, "$1 с"),
        (#"^Scene “(.+)”: the TV doesn't report its volume — step skipped\.$"#,
         "Сцена «$1»: ТВ не сообщает громкость — шаг пропущен."),
        (#"^TV “(.+)” not found — the scene ran on the current TV\.$"#,
         "ТВ «$1» не найден — сцена выполнена на текущем ТВ."),
    ].map { (try! NSRegularExpression(pattern: $0.0, options: [.dotMatchesLineSeparators]), $0.1) }
}
