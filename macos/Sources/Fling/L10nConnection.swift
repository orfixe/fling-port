// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE

/// Port: the TV connection's words (remote errors, offline TVs, pairing), kept
/// apart from the main table in L10n.swift. Russian as in the Windows / Linux build.
enum PlainWords {
    /// Upstream messages shown in plainer English too (they stay the keys in FlingKit).
    static let english: [String: String] = [
        "No device selected": "No TV found yet — check that the TV is on and on the same Wi-Fi as this Mac.",
    ]

    static let russian: [String: String] = [
        "Pair with TV": "Связать с ТВ",
        "Online": "В сети",
        "Offline": "Не в сети",
        "Offline — Fling can't reach this TV": "Не в сети — Fling не достаёт до этого ТВ",
        "Checking…": "Проверяю связь…",
        "“{a}” is offline — switch to “{b}”": "«{a}» не в сети — переключиться на «{b}»",
        "There's no link on the clipboard.": "В буфере обмена нет ссылки.",
        "No TV found yet — check that the TV is on and on the same Wi-Fi as this Mac.":
            "Телевизор пока не найден — проверьте, что он включён и в той же сети Wi-Fi, что и этот Mac.",
        "The TV has forgotten this Mac — it happens after a TV reset or update. Click “Pair with TV” and type the code from the TV screen.":
            "Телевизор забыл этот Mac — так бывает после сброса или обновления ТВ. Нажмите «Связать с ТВ» и введите код с экрана телевизора.",
        "The remote isn't paired with the TV. Click “Pair with TV” and type the code from the TV screen.":
            "Пульт не связан с телевизором. Нажмите «Связать с ТВ» и введите код с экрана ТВ.",
        "The remote isn't answering: the TV is off or on another network. Try again when the TV is on.":
            "Пульт не отвечает: телевизор выключен или в другой сети. Попробуйте ещё раз, когда ТВ включится.",
        "The connection to the TV remote dropped. Press again to reconnect.":
            "Связь с пультом ТВ прервалась. Нажмите ещё раз — Fling подключится заново.",
        "The TV didn't answer in time. Try again.": "Телевизор не ответил вовремя. Попробуйте ещё раз.",
        "Reconnecting to the TV…": "Подключаюсь к ТВ заново…",
        "Fling couldn't prepare this Mac's remote key. Try “Pair with TV” again.":
            "Fling не смог подготовить ключ пульта на этом Mac. Нажмите «Связать с ТВ» ещё раз.",
        "The TV remote didn't work this time. Try again.": "Пульт ТВ сейчас не сработал. Попробуйте ещё раз.",
    ]
}
