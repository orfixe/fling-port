// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
//
// Port: Russian for the Tools window and the Catalog (the phrases of desktop/fling_tools.py
// and desktop/fling_catalog.py). tr() in L10n.swift looks here too.

enum ToolsRU {
    static let exact: [String: String] = [
        // window, tools tab
        "Fling — tools": "Fling — инструменты",
        "Tools & Catalog…": "Инструменты и каталог…",
        "Tools": "Инструменты",
        "Catalog": "Каталог",
        "Programs Fling uses for casting, ADB and the TV screen. If they're already on this Mac, Fling finds them; if not, it can install them with Homebrew (catt with uv).":
            "Программы, которыми Fling транслирует, работает с ADB и показывает экран ТВ. Если они уже есть на Mac, Fling их найдёт; если нет — установит через Homebrew (catt — через uv).",
        "Homebrew: {path}": "Homebrew: {path}",
        "Homebrew isn't installed.": "Homebrew не установлен.",
        "How to install Homebrew": "Как установить Homebrew",
        "Search again": "Найти заново",
        "Searching this Mac…": "Ищу на этом Mac…",
        "Not found on this Mac.": "Не найден на этом Mac.",
        "Sending videos and links to the TV over Cast.": "Отправка видео и ссылок на ТВ по Cast.",
        "ADB: what's playing on the TV, screenshots, installing APKs and the ADB console.":
            "ADB: что играет на ТВ, снимки экрана, установка APK и ADB-консоль.",
        "The TV screen in a Fling window and the computer's mouse on the TV (LIVE).":
            "Экран ТВ в окне Fling и мышь компьютера на ТВ (LIVE).",
        "Install uv (https://docs.astral.sh/uv/), then run in Terminal: uv tool install catt":
            "Установите uv (https://docs.astral.sh/uv/), затем выполните в Терминале: uv tool install catt",
        "Download “SDK Platform-Tools for Mac” from Google, unzip it and put the platform-tools folder in your home folder, or install Homebrew (https://brew.sh) and press Install here.":
            "Скачайте «SDK Platform-Tools for Mac» с сайта Google, распакуйте и положите папку platform-tools в свою домашнюю папку — или установите Homebrew (https://brew.sh) и нажмите здесь «Установить».",
        "Install Homebrew (https://brew.sh) and press Install here, or download the macOS build from the scrcpy releases page.":
            "Установите Homebrew (https://brew.sh) и нажмите здесь «Установить» — или скачайте сборку для macOS со страницы релизов scrcpy.",
        "Google's page": "страница Google",
        "scrcpy releases": "релизы scrcpy",
        "Installing…": "Устанавливаю…",
        "Not found": "Не найден",
        "Found": "Найден",
        "Outdated": "Устарел",
        "version {v}": "версия {v}",
        "version ?": "версия ?",
        "Version {v} or newer is needed.": "Нужна версия {v} или новее.",
        "Checking the latest version…": "Узнаю последнюю версию…",
        "{v} available": "доступна {v}",
        "Latest version {v} — the latest version": "Последняя версия {v} — это последняя версия",
        "Latest version {v}": "Последняя версия {v}",
        "Install": "Установить",
        "Update": "Обновить",
        "Update to {v}": "Обновить до {v}",
        "Runs: {cmd}": "Команда: {cmd}",
        "Check for an update": "Проверить обновление",
        "Cancel": "Отмена",
        "Done.": "Готово.",
        "It didn't work (exit code {code}).": "Не вышло (код выхода {code}).",
        "Licence": "Лицензия",
        "Project website": "Сайт проекта",
        // catalog tab
        "Extra modules, from official sources only. Before installing, Fling checks every file against the publisher's checksum or signed catalogue, and for TV apps also the developer's signing key.":
            "Дополнительные модули — только из официальных источников. Перед установкой Fling сверяет каждый файл с контрольной суммой издателя или его подписанным каталогом, а у приложений для ТВ проверяет ещё и ключ разработчика.",
        "TV apps": "Приложения для ТВ",
        "Refresh": "Обновить",
        "Install on TV": "Установить на ТВ",
        "Remove from TV": "Удалить с ТВ",
        "Open": "Открыть",
        "Remove": "Удалить",
        "Opening on the TV…": "Открываю на ТВ…",
        "On the TV": "Установлено на ТВ",
        "On the TV: version {v}": "На ТВ версия {v}",
        "On the TV: version unknown": "На ТВ: версия неизвестна",
        " for {abi}": " для {abi}",
        "MB": "МБ",
        "Source:": "Источник:",
        "Couldn't check the version: ": "Не удалось узнать версию: ",
        "No TV chosen — TV apps can be installed once Fling finds a TV.":
            "Телевизор не выбран — приложения для ТВ можно будет установить, когда Fling найдёт телевизор.",
        "{name}: ADB isn't connected. TV apps are installed over ADB.":
            "{name}: ADB не подключён. Приложения на ТВ устанавливаются через ADB.",
        "{name}: checking ADB…": "{name}: проверяю ADB…",
        "{name} · ADB connected": "{name} · ADB подключён",
        "Android {v}": "Android {v}",
        "processor {abi}": "процессор {abi}",
        "Connect ADB": "Подключить ADB",
        "Connecting…": "Подключаю…",
        "Connect ADB first: the “Connect ADB” button at the top.":
            "Сначала подключите ADB: кнопка «Подключить ADB» вверху.",
        "Remove {name} from the TV?": "Удалить {name} с телевизора?",
        "The app's settings and data on the TV are removed too.": "Настройки и данные приложения на ТВ тоже удалятся.",
        "Installed on the TV: {name} {version}. The developer's signature checked out.":
            "Установлено на ТВ: {name} {version}. Подпись разработчика проверена.",
        "Removed from the TV: {name}.": "Удалено с ТВ: {name}.",
        "Cancelled.": "Отменено.",
        "Failed: ": "Не вышло: ",
        "Loading the F-Droid catalogue: {done} of {total} MB": "Загружаю каталог F-Droid: {done} из {total} МБ",
        "Downloading: {done} of {total} MB": "Скачиваю: {done} из {total} МБ",
        "Downloading: {done} MB": "Скачиваю: {done} МБ",
        "Verifying the checksum…": "Проверяю контрольную сумму…",
        "Checking the app's signature…": "Проверяю подпись приложения…",
        "Installing on the TV — this can take a couple of minutes…": "Устанавливаю на ТВ — это может занять пару минут…",
        "Checking that the app is on the TV…": "Проверяю, что приложение появилось на ТВ…",
        "Removing from the TV…": "Удаляю с ТВ…",
        // purposes
        "A player that opens almost any file or stream — for when Cast can't handle the format.":
            "Плеер, который откроет почти любой файл и поток — когда Cast не справляется с форматом.",
        "A media centre: films, series and music from your home network, with artwork and descriptions.":
            "Медиацентр: фильмы, сериалы и музыка из домашней сети с обложками и описаниями.",
        "The client for your own Jellyfin media server: your film and series library on the TV.":
            "Клиент для своего медиасервера Jellyfin: библиотека фильмов и сериалов на ТВ.",
        "Your computer's games and desktop on the TV, via Sunshine or NVIDIA GameStream.":
            "Игры и рабочий стол компьютера на ТВ через Sunshine или NVIDIA GameStream.",
        "Sends files between the TV, phones and computers over Wi-Fi, no internet needed.":
            "Передача файлов между ТВ, телефоном и компьютером по Wi-Fi, без интернета.",
        "Your own private network: reach the TV from your phone and computer even away from home. After installing, open it on the TV and sign in.":
            "Своя защищённая сеть: ТВ виден с телефона и компьютера даже вне дома. После установки откройте его на ТВ и войдите в аккаунт.",
        "A file manager: open what Fling copied to the TV's Download folder.":
            "Файловый менеджер: открыть то, что Fling скопировал на ТВ в папку Download.",
        "A store of free apps: other open-source programs and their updates.":
            "Магазин свободных приложений: остальные программы с открытым кодом и их обновления.",
        // errors (FlingKit: CatalogText, CatalogInstaller)
        "the file doesn't look like an Android app (APK)": "файл не похож на приложение Android (APK)",
        "the APK isn't signed — not installing": "APK не подписан — устанавливать не буду",
        "the APK has no app description (AndroidManifest)": "в APK нет описания приложения (AndroidManifest)",
        "the official server redirected the checksum request — cancelled":
            "официальный сервер перенаправил запрос контрольной суммы — отменено",
        "the publisher has no build for this TV's processor": "у издателя нет сборки для процессора этого ТВ",
        "the publisher's checksums don't match — install cancelled": "контрольные суммы издателя не совпадают — установка отменена",
        "the download link points somewhere else — cancelled": "ссылка на загрузку ведёт не туда — отменено",
        "the publisher provides no checksum — not installing": "издатель не дал контрольную сумму — устанавливать не буду",
        "the F-Droid catalogue signature doesn't match — cancelled": "подпись каталога F-Droid не сходится — отменено",
        "F-Droid sent an outdated catalogue — cancelled": "F-Droid прислал устаревший каталог — отменено",
        "the F-Droid catalogue is damaged: the checksum didn't match": "каталог F-Droid повреждён: контрольная сумма не сошлась",
        "this app isn't in the F-Droid catalogue": "в каталоге F-Droid нет этого приложения",
        "downloads are allowed over HTTPS only": "загрузка возможна только по HTTPS",
        "the server redirected the download away from HTTPS — cancelled": "сервер перенаправил загрузку не на HTTPS — отменено",
        "the server's response is too large": "ответ сервера слишком большой",
        "the archive is suspiciously large — cancelled": "архив подозрительно большой — отменено",
        "the download was interrupted — press again": "загрузка оборвалась — нажмите ещё раз",
        "the download is damaged: the checksum didn't match": "файл повреждён: контрольная сумма не сошлась",
        "can't reach the download server": "нет связи с сервером загрузки",
        "the file holds a different app — install cancelled": "в файле другое приложение — установка отменена",
        "the developer's key is unknown — not installing": "неизвестен ключ разработчика — устанавливать не буду",
        "the app isn't signed with the developer's key — install cancelled":
            "приложение подписано не ключом разработчика — установка отменена",
        "the TV didn't answer over ADB": "ТВ не ответил по ADB",
        "the TV reported success, but the app isn't there": "ТВ сообщил об успехе, но приложения на нём нет",
        "the TV didn't remove the app": "ТВ не удалил приложение",
        "no TV is chosen": "телевизор не выбран",
        "adb isn't installed — install it in the Tools tab.": "adb не установлен — установите его на вкладке «Инструменты».",
        "ADB isn't connected. Turn on network debugging on the TV and connect ADB first.":
            "ADB не подключён. Включите отладку по сети на ТВ и сначала подключите ADB.",
        "The TV hasn't allowed this Mac yet — confirm the ADB prompt on the TV screen.":
            "ТВ ещё не разрешил этот Mac — подтвердите запрос ADB на экране ТВ.",
    ]

    static let installPrefix = "the TV didn't install the app: "
    static let installErrors: [String: String] = [
        "the TV already has this app with a different signature (from Google Play, for example) — remove it on the TV and try again.":
            "на ТВ уже стоит это приложение с другой подписью (например, из Google Play) — удалите его на ТВ и повторите.",
        "the TV already has a newer version.": "на ТВ уже стоит более новая версия.",
        "this build doesn't fit the TV's processor.": "эта сборка не подходит к процессору ТВ.",
        "the app needs a newer Android than the TV has.": "приложению нужен более новый Android, чем на ТВ.",
        "the TV is out of space.": "на ТВ не хватает места.",
        "the TV blocked the install — allow it on the TV screen.": "ТВ запретил установку — подтвердите её на экране ТВ.",
        "the TV rejected the app (Google Play Protect check).": "ТВ не пропустил приложение (проверка Google Play Protect).",
        "no answer": "нет ответа",
    ]

    static func lookup(_ text: String) -> String? {
        if let ru = exact[text] { return ru }
        if text.hasPrefix(installPrefix) {
            let rest = String(text.dropFirst(installPrefix.count))
            return "ТВ не установил приложение: " + (installErrors[rest] ?? rest)
        }
        if text.hasPrefix("the server responded with code ") {
            return "сервер ответил кодом " + text.dropFirst("the server responded with code ".count)
        }
        return nil
    }
}
