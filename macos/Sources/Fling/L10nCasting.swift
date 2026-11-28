// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE

/// Port: Russian for the casting & settings features (browser bridge, hotkey, queue,
/// notifications, Settings). Kept apart from L10n.swift's table; `tr()` looks in both.
/// Keys with %@ / %d are formats: `String(format: tr(key), …)`.
enum RUCasting {
    static let exact: [String: String] = [
        // notifications, queue
        "Now on TV: %@": "Сейчас на ТВ: %@",
        "Not a media file: %@": "Не отправлю %@: это не видео, музыка или фото",
        "Nothing to play": "Нечего воспроизвести",
        "No photos in this folder": "В папке нет фото",
        "Slideshow “%@”: a new photo every %d s": "Слайд-шоу «%@»: смена каждые %d с",
        "Queue: %d items — starting with “%@”": "Очередь: %d — начинаю с «%@»",
        "That's the last item in the queue": "Это последний файл в очереди",
        "Slideshow finished": "Слайд-шоу закончилось",
        "Queue finished": "Очередь закончилась",
        "Resuming at %@: %@": "Продолжаю с %@: %@",
        "Couldn't open the picture: %@": "Не удалось открыть изображение: %@",
        "The TV can't play this format or codec": "ТВ не может воспроизвести этот формат или кодек",
        "Couldn't play %@: %@": "Не получилось воспроизвести %@: %@",
        "Skipping %@: %@": "Пропускаю %@: %@",
        "Choose a folder with photos": "Выберите папку с фото",
        "Choose videos, music, photos, playlists or folders": "Выберите видео, музыку, фото, плейлисты или папки",
        "Play on TV": "Показать на ТВ",
        // panel, menu
        "Cast from %@: %@": "Отправить из %@: %@",
        "Files from this Mac to the TV…": "Файлы с этого Mac на ТВ…",
        "Photo slideshow…": "Слайд-шоу из фото…",
        "Previous": "Предыдущий",
        "Next": "Следующий",
        "Resume slideshow": "Продолжить слайд-шоу",
        "Pause slideshow": "Пауза слайд-шоу",
        "Resume the queue": "Продолжить очередь",
        "Clear the queue": "Очистить очередь",
        "Next in queue": "Следующий в очереди",
        "Previous in queue": "Предыдущий в очереди",
        "The queue is waiting — press ▶ to carry on.": "Очередь ждёт — нажмите ▶, чтобы продолжить.",
        "Firefox, Edge, Brave…: add the Fling browser extension to cast their tabs, from where the video is.":
            "Firefox, Edge, Brave…: установите расширение Fling, чтобы отправлять их вкладки — с того же места видео.",
        "How": "Как",
        "Hide": "Скрыть",
        "Settings…": "Настройки…",
        // Settings
        "Fling Settings": "Настройки Fling",
        "General": "Общие",
        "Casting": "Трансляция",
        "Files & queue": "Файлы и очередь",
        "Language": "Язык",
        "As the system": "Как в системе",
        "Fling restarts in the chosen language.": "Fling перезапустится на выбранном языке.",
        "Casting browser tabs": "Отправка вкладок браузера",
        "The tab, “Cast this tab” and the browser extension hint in the panel.":
            "Вкладка, кнопка «Отправить эту вкладку» и подсказка про расширение в панели.",
        "Browser extension hint": "Подсказка про расширение браузера",
        "Notify when the video changes": "Уведомлять о смене видео",
        "When the TV starts another video or track and the panel is closed.":
            "Когда на ТВ начинается другое видео или трек, а панель закрыта.",
        "Cast the current tab": "Отправить вкладку",
        "Works everywhere, even with the panel closed. Chrome and Safari are read directly; other browsers through the Fling extension. ⌘⇧C works too once Fling has Accessibility access.":
            "Работает везде, даже когда панель закрыта. Chrome и Safari Fling читает сам, другие браузеры — через расширение Fling. ⌘⇧C тоже работает, если дать Fling доступ к Универсальному доступу.",
        "Link with the browser extension": "Связь с расширением браузера",
        "The extension tells Fling which tab is open, casts it from where the page's video is, and adds a Fling button to the browser. Takes effect after Fling restarts.":
            "Расширение сообщает Fling, какая вкладка открыта, отправляет её с того же места видео и добавляет кнопку Fling в браузер. Изменение вступит в силу после перезапуска Fling.",
        "The bridge couldn't start: %@": "Связь с расширением не запустилась: %@",
        "Listening on 127.0.0.1:%d — this Mac only.": "Слушаю 127.0.0.1:%d — только этот Mac.",
        "Install the extension": "Установка расширения",
        "Chrome, Edge, Brave, Opera, Vivaldi: open the browser's extensions page, turn on Developer mode, press Load unpacked and choose the “chrome” folder.":
            "Chrome, Edge, Brave, Opera, Vivaldi: откройте страницу расширений браузера, включите режим разработчика, нажмите «Загрузить распакованное» и выберите папку «chrome».",
        "Firefox: about:debugging → This Firefox → Load Temporary Add-on… → manifest.json in the “firefox” folder (until Firefox restarts).":
            "Firefox: about:debugging → Этот Firefox → «Загрузить временное дополнение…» → manifest.json в папке «firefox» (до перезапуска Firefox).",
        "Safari: build it with extension/safari/build-safari.sh on a Mac with Xcode.":
            "Safari: соберите его скриптом extension/safari/build-safari.sh на Mac с Xcode.",
        "Show extension folder": "Показать папку расширения",
        "Open Chrome extensions": "Расширения Chrome",
        "Open Edge extensions": "Расширения Edge",
        "✓ An extension has connected to Fling.": "✓ Расширение уже подключалось к Fling.",
        "Repeat": "Повтор",
        "Off": "Выкл",
        "The whole queue": "Вся очередь",
        "One item": "Один файл",
        "Shuffle": "Перемешать",
        "Slideshow: next photo after": "Слайд-шоу: следующее фото через",
        "%d s": "%d с",
        "Resume where you left off": "Продолжать с того же места",
        "Films and episodes of 10 minutes or more continue from the same place next time.":
            "Фильмы и серии от 10 минут в следующий раз продолжатся с того же места.",
        "Files from this Mac: videos (mp4, m4v, mov, webm, mkv), music, photos (heic, webp and tiff are converted), m3u / pls playlists and whole folders. Subtitles next to a video (film.srt, film.en.srt, Subs/…) are picked up automatically; srt and ass are converted for the TV.":
            "Файлы с этого Mac: видео (mp4, m4v, mov, webm, mkv), музыка, фото (heic, webp и tiff конвертируются), плейлисты m3u / pls и целые папки. Субтитры рядом с видео (film.srt, film.ru.srt, Subs/…) подхватываются сами; srt и ass конвертируются для ТВ.",
    ]
}
