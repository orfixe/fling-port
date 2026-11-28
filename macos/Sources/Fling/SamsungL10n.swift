// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation
import FlingKit

/// Port: Russian for the Samsung (Smart View) strings; `tr()` looks here after
/// the main table. Wording follows desktop/fling_samsung.py's PHRASES.
enum SamsungRU {
    static let exact: [String: String] = [
        "Google TV / Android TV": "Google TV / Android TV",
        "Chromecast": "Chromecast",
        "Samsung": "Samsung",
        "Samsung · network remote": "Samsung · пульт по сети",
        "Connecting to the TV…": "Подключаюсь к ТВ…",
        "Samsung · the TV doesn't answer": "Samsung · ТВ не отвечает",
        "Samsung · not allowed on the TV": "Samsung · ТВ не разрешил подключение",
        "Turning the TV on…": "Включаю ТВ…",
        "Mirror screen (AirPlay)…": "Зеркалировать экран (AirPlay)…",
        SamsungMirror.hint:
            "В «Мониторах» нажмите + и выберите ТВ в разделе AirPlay, или Пункт управления ▸ «Повтор экрана». Samsung с 2018 года принимает AirPlay; Miracast на macOS нет.",
        SamsungText.pressAllow: "Нажмите «Разрешить» на экране ТВ",
        SamsungText.denied:
            "ТВ не разрешил подключение. В настройках ТВ: Общие → Диспетчер внешних устройств → Диспетчер подключения устройств — разрешите Fling.",
        SamsungText.noAnswer: "ТВ не отвечает — он выключен или в другой сети.",
        SamsungText.unknownApp: "Это приложение Fling не знает на Samsung — откройте его пультом.",
        SamsungText.noMAC:
            "Не знаю MAC-адрес ТВ — включите его один раз пультом, чтобы Fling его запомнил.",
        SamsungText.powerOnHint:
            "Чтобы включать по сети, на ТВ: Общие → Сеть → Экспертные настройки → «Включение с моб. устройства».",
        SamsungText.noDLNA: "ТВ не поддерживает DLNA или он выключен.",
        SamsungText.noVoice: "Голосовой поиск на Samsung недоступен — наберите запрос текстом.",
    ]

    static let patterns: [(NSRegularExpression, String)] = [
        (#"^The TV didn't accept the video \((.*)\)\.$"#, "ТВ не принял видео ($1)."),    ].map { (try! NSRegularExpression(pattern: $0.0, options: [.dotMatchesLineSeparators]), $0.1) }
}
