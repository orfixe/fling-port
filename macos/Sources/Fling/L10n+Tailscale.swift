// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation

/// Port: Russian for remote access over Tailscale (Tailscale.swift, RemoteAccessView.swift),
/// the same wording as desktop/fling_tailscale.py. Looked up by tr() after RU.exact.
enum TailscaleRU {
    static let exact: [String: String] = [
        "Remote access (Tailscale)": "Удалённый доступ (Tailscale)",
        "Remote access (Tailscale)…": "Удалённый доступ (Tailscale)…",
        "TVs on Tailscale…": "ТВ в сети Tailscale…",
        "TVs": "ТВ",
        "Refresh": "Обновить",
        "Checking Tailscale…": "Проверяю Tailscale…",
        "Tailscale isn't installed on this Mac.": "Tailscale не установлен на этом Mac.",
        "With Tailscale, the remote works away from home. Install it on this Mac and on the TV, and sign in to the same account on both.":
            "С Tailscale пульт работает вдали от дома. Установите его на Mac и на ТВ и войдите в один и тот же аккаунт.",
        "Couldn't read the Tailscale status.": "Не удалось прочитать состояние Tailscale.",
        "You aren't signed in to Tailscale.": "Вход в Tailscale не выполнен.",
        "Tailscale is off.": "Tailscale выключен.",
        "Turn it on and sign in from the Tailscale icon in the menu bar, then press Refresh. Fling doesn't switch Tailscale on by itself.":
            "Включите его и войдите через значок Tailscale в строке меню, затем нажмите «Обновить». Сам Fling Tailscale не включает.",
        "Tailscale is on": "Tailscale включён",
        "TVs on your tailnet": "Телевизоры в сети Tailscale",
        "There are no TVs on your tailnet. Install Tailscale on the TV (Google Play) and sign in to the same account as on this Mac.":
            "В сети Tailscale нет телевизоров. Установите Tailscale на ТВ (Google Play) и войдите в тот же аккаунт, что и на этом Mac.",
        "TVs only": "Только телевизоры",
        "online": "в сети",
        "offline": "не в сети",
        "selected": "выбран",
        "Select": "Выбрать",
        "Add": "Добавить",
        "Remove": "Убрать",
        "no casting": "без трансляции",
        "Add this TV and control it over Tailscale": "Добавить этот ТВ и управлять им через Tailscale",
        "The TV isn't on the tailnet right now": "ТВ сейчас не в сети Tailscale",
        "Add a TV by address": "Добавить ТВ по адресу",
        "100.x.y.z or MagicDNS name": "100.x.y.z или имя MagicDNS",
        "Added. Its Cast port doesn't answer over Tailscale, so casting won't work — the remote does.":
            "Добавлен. Порт Cast на нём через Tailscale не отвечает, поэтому трансляция не заработает — а пульт работает.",
        "This TV was added via Tailscale and its Cast port doesn't answer, so casting isn't available. The remote, apps and ADB work.":
            "Этот ТВ добавлен через Tailscale, и порт Cast на нём не отвечает — трансляция недоступна. Пульт, приложения и ADB работают.",
        "What works away from home": "Что работает вдали от дома",
        "The remote, TV volume, apps, typing and voice search (the TV's ports 6466/6467)":
            "Пульт, громкость ТВ, приложения, набор текста и голосовой поиск (порты ТВ 6466/6467)",
        "ADB and the TV's live screen, where this build has them: they connect by address too":
            "ADB и экран ТВ вживую — там, где они есть в этой сборке: они тоже подключаются по адресу",
        "Casting tabs and links (catt) needs the home network: away from home it works only if the TV answers on the Cast port (8009), which most don't":
            "Трансляции вкладок и ссылок (catt) нужна домашняя сеть: вдали от дома она работает, только если ТВ отвечает на порт Cast (8009), а большинство не отвечает",
        "Finding TVs on the network (mDNS stays at home): add the TV by its Tailscale address":
            "Поиск ТВ в сети (mDNS остаётся дома) — добавляйте ТВ по адресу Tailscale",
        "Security": "Безопасность",
        "ADB over Tailscale is full access to the TV": "ADB через Tailscale — полный доступ к ТВ",
        "In your Tailscale access rules (ACLs), let only your own devices reach the TV's ports 5555, 6466 and 6467. Fling opens nothing to the internet and forwards no ports.":
            "Разрешите в правилах доступа (ACL) Tailscale подключаться к портам ТВ 5555, 6466 и 6467 только своим устройствам. Fling ничего не открывает в интернет и не пробрасывает порты.",
        "Tag the TV as tag:tv in the Tailscale admin console (Machines → the TV → Edit ACL tags) and paste the rule below. If your rules allow everything to everyone (src \"*\" → dst \"*:*\"), remove that rule, or every device on the tailnet can reach the TV.":
            "Пометьте ТВ тегом tag:tv в консоли Tailscale (Machines → ТВ → Edit ACL tags) и вставьте правило ниже. Если в ваших правилах есть «всё всем» (src \"*\" → dst \"*:*\"), уберите его, иначе ТВ видят все устройства сети.",
        "Copy an example ACL": "Скопировать пример ACL",
        "Copied": "Скопировано",
        "Download Tailscale": "Скачать Tailscale",
        "How it works": "Как это работает",
        "Fling only reads the Tailscale status and never changes its settings.":
            "Fling только читает состояние Tailscale и никогда не меняет его настройки.",
        "via Tailscale": "через Tailscale",
        "via Tailscale · no connection": "через Tailscale · нет связи",
        "The TV isn't answering over Tailscale. The remote connects again by itself once the connection is back.":
            "ТВ не отвечает через Tailscale. Пульт переподключится сам, когда связь вернётся.",
        "Tailscale: checking the connection to the TV…": "Tailscale: проверяю связь с ТВ…",
        "Tailscale: a direct connection to the TV.": "Tailscale: прямое соединение с ТВ.",
        "Tailscale: the TV answers.": "Tailscale: ТВ отвечает.",
    ]

    static let patterns: [(NSRegularExpression, String)] = [
        (#"^via Tailscale · (\d+) ms$"#, "через Tailscale · $1 мс"),
        (#"^This Mac: (.+)$"#, "Этот Mac: $1"),
        (#"^Account: (.+)$"#, "Аккаунт: $1"),
        (#"^Show all devices \((\d+)\)$"#, "Показать все устройства ($1)"),
        (#"^Checking (.+) over Tailscale…$"#, "Проверяю $1 через Tailscale…"),
        (#"^Home network through (.+?): (.+)\. Add a TV there by its home address below\.$"#,
         "Домашняя сеть через $1: $2. ТВ в ней добавляйте по домашнему адресу ниже."),
        (#"^Neither Cast, the remote nor ADB answers at (.+)\. Is Tailscale running on the TV\?$"#,
         "По адресу $1 не отвечают ни Cast, ни пульт, ни ADB. Включён ли Tailscale на ТВ?"),
        (#"^Couldn't find “(.+)” on the tailnet or in DNS\.$"#, "Не нашёл «$1» ни в сети Tailscale, ни в DNS."),
        (#"^Tailscale: through the (.+) relay, so the delay is higher than with a direct connection\.$"#,
         "Tailscale: через ретранслятор $1 — задержка выше, чем напрямую."),
    ].map { (try! NSRegularExpression(pattern: $0.0, options: [.dotMatchesLineSeparators]), $0.1) }
}
