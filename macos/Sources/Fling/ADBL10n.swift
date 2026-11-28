// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation

/// Port: Russian for the ADB strings (ADBView.swift, FlingKit/ADB*.swift),
/// kept apart from L10n.swift's table; `tr()` asks here too.
enum ADBRU {
    static func translate(_ text: String) -> String? {
        if let ru = exact[text] { return ru }
        // "Prefix: rest" — the rest is translated on its own (a reason, a path, a name).
        for (en, ru) in prefixes where text.hasPrefix(en) {
            let rest = String(text.dropFirst(en.count))
            return ru + (exact[rest] ?? rest)
        }
        for (en, ru) in suffixes where text.hasSuffix(en) {
            let head = String(text.dropLast(en.count))
            return (exact[head] ?? head) + ru
        }
        return nil
    }

    private static let prefixes: [(String, String)] = [
        ("Connected (port ", "Подключено (порт "),
        ("TV screenshot saved: ", "Снимок экрана ТВ сохранён: "),
        ("Installing on the TV: ", "Устанавливаю на ТВ: "),
        ("Installed on the TV: ", "Установлено на ТВ: "),
        ("APK install failed: ", "Установка APK не удалась: "),
        ("The screenshot didn't work: ", "Снимок экрана не получился: "),
        ("Didn't work: ", "Не вышло: "),
        ("On the TV: ", "На ТВ: "),
    ]

    private static let suffixes: [(String, String)] = [
        (": adb isn't installed on this Mac.", ": adb не установлен на этом Mac."),
        (" works over ADB — connect ADB first.", " работает через ADB — сначала подключите ADB."),
    ]

    private static let exact: [String: String] = [
        "ADB": "ADB",
        "Connect": "Подключить",
        "Try again": "Повторить",
        "Copy": "Скопировать",
        "Pair": "Связать",
        "IP:": "IP:",
        "Port:": "Порт:",
        "pairing:": "сопряжения:",
        "port": "порт",
        "6 digits from the TV": "6 цифр с экрана ТВ",
        "No TV chosen": "ТВ не выбран",
        "Not connected": "Не подключено",
        "adb isn't installed": "adb не установлен",
        "Connecting…": "Подключаюсь…",
        "Waiting for “Allow” on the TV…": "Ждём «Разрешить» на экране ТВ…",
        "The TV asks for a code": "ТВ просит код",
        "Debugging is off on the TV": "Отладка на ТВ выключена",
        "Didn't work": "Не вышло",
        "TV screenshot": "Снимок экрана ТВ",
        "Install APK on the TV…": "Установить APK на ТВ…",
        "Install an APK on the TV": "Установить APK на ТВ",
        "Disconnect ADB": "Отключить ADB",
        "Hide the by-hand fields": "Скрыть ручные поля",
        "By hand: IP and port…": "Вручную: IP и порт…",
        "Usually not needed: Fling finds the ports itself. On the TV: Wireless debugging → IP address & Port.":
            "Обычно не нужно: Fling находит порты сам. С ТВ: Беспроводная отладка → «IP-адрес и порт».",
        "ADB shows what really plays on the TV, takes screenshots and installs apps.":
            "Через ADB видно, что на самом деле играет на ТВ, можно снять экран и установить приложение.",
        "Fling uses adb from Android platform-tools. Install it in Terminal, then press Try again:":
            "Fling работает с adb из Android platform-tools. Установите его в Терминале и нажмите «Повторить»:",
        "Look at the TV screen: press “Allow” and tick “Always allow from this computer”. Fling connects by itself as soon as you do.":
            "Посмотрите на экран ТВ: нажмите «Разрешить» и отметьте «Всегда разрешать с этого компьютера». Fling подключится сам, как только вы нажмёте.",
        "The TV shows pairing — type the 6 digits from the TV screen and press Pair.":
            "На экране ТВ открыто сопряжение — впишите 6 цифр с экрана и нажмите «Связать».",
        "Turn on debugging on the TV — Fling notices by itself:\n1. Settings → About → press “Build” 7 times.\n2. Settings → Developer options → “Network debugging” (or “USB debugging”).\nAndroid 11+: “Wireless debugging” → “Pair device with pairing code” — the code shows on the TV, type it here.":
            "Включите на ТВ отладку — Fling заметит это сам:\n1. Настройки → Об устройстве → 7 раз нажмите «Сборка».\n2. Настройки → Для разработчиков → «Отладка по сети» (или «Отладка по USB»).\nНа Android 11+: «Беспроводная отладка» → «Подключить по коду» — код появится на ТВ, впишите его здесь.",
        "Playing": "Воспроизводится",
        "Playing on the TV": "Играет на ТВ",
        "Paused on the TV": "Пауза на ТВ",
        "Loading on the TV…": "Загружается на ТВ…",
        "On the TV": "На ТВ",
        // FlingKit/ADBController.swift
        "The TV's IP is four numbers with dots, e.g. 192.168.1.50.":
            "IP ТВ — четыре числа через точку (пример: 192.168.1.50).",
        "Choose a TV first (or type its IP by hand).": "Сначала выберите телевизор (или впишите IP вручную).",
        "The pairing code is the 6 digits on the TV screen.": "Код сопряжения — 6 цифр с экрана ТВ.",
        "The code window on the TV has closed. On the TV: Wireless debugging → Pair device with pairing code, then type the new code.":
            "Окно с кодом на ТВ уже закрыто. На ТВ: Беспроводная отладка → «Подключить по коду» — и впишите новый код.",
        "ADB disconnected": "ADB отключён",
        "The screenshot is black: the video is protected from capture (DRM in YouTube / Netflix). Menus and the home screen capture fine.":
            "Снимок чёрный: видео защищено от снимков (DRM у YouTube/Netflix). Меню и главный экран ТВ снимаются нормально.",
        "That's not an APK file.": "Это не APK-файл.",
        "No TV chosen — click Choose at the bottom of the panel.": "Телевизор не выбран — выберите его внизу панели.",
        "Screenshot": "Снимок экрана",
        "APK install": "Установка APK",
        "adb didn't start": "adb не запустился",
        "no data": "нет данных",
        // FlingKit/ADBParsing.swift
        "The TV didn't answer.": "ТВ не ответил.",
        "The TV didn't answer in 10 minutes.": "ТВ не ответил за 10 минут.",
        "The TV already has this app with a different signature — remove it on the TV and try again.":
            "на ТВ уже есть это приложение с другой подписью — удалите его на ТВ и попробуйте снова.",
        "The TV already has a newer version.": "на ТВ уже стоит более новая версия.",
        "This APK is built for another processor — the TV needs an armeabi-v7a / arm64 build.":
            "этот APK собран для другого процессора — нужна версия для armeabi-v7a / arm64.",
        "This APK needs a newer Android than the TV has.": "этот APK требует более новый Android, чем на ТВ.",
        "The TV is out of space.": "на ТВ не хватает места.",
        "The APK isn't signed.": "APK не подписан.",
        "The file is damaged or isn't an APK.": "файл повреждён или это не APK.",
        "The app comes in several APKs — choose them all at once.":
            "приложение состоит из нескольких APK — выберите их все сразу.",
        "The TV refused the install — confirm it on the TV screen.":
            "ТВ запретил установку — подтвердите её на экране ТВ.",
    ]
}
