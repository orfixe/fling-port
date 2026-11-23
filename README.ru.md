<p align="right">
  <a href="README.md">🇬🇧 English</a> &nbsp;·&nbsp; <a href="README.ru.md">🇷🇺 Русский</a>
</p>

<div align="center">

<img src="docs/screenshots/logo.png" width="96" alt="Логотип Fling">

# Fling для Windows, Linux и Android

**Ваш телевизор — из трея Windows, панели KDE, браузера и телефона.**

Отправляйте вкладку браузера или ссылку на Chromecast / Google TV — и управляйте самим
телевизором: питание, приложения, громкость, ввод текста, голосовой поиск.

[![Windows 10/11](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D6?logo=windows&logoColor=white)](docs/install-windows.ru.md)
[![Linux KDE](https://img.shields.io/badge/Linux-KDE%20Plasma-1D99F3?logo=kde&logoColor=white)](#linux)
[![Android 8+](https://img.shields.io/badge/Android-8%2B-3DDC84?logo=android&logoColor=white)](#android)
<br>
[![Chrome / Edge](https://img.shields.io/badge/Chrome%20%2F%20Edge-расширение-4285F4?logo=googlechrome&logoColor=white)](#расширение-для-браузера)
[![Firefox](https://img.shields.io/badge/Firefox-расширение-FF7139?logo=firefoxbrowser&logoColor=white)](#расширение-для-браузера)
[![Safari](https://img.shields.io/badge/Safari-расширение-006CFF?logo=safari&logoColor=white)](#расширение-для-браузера)
[![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

<img src="docs/screenshots/hero.png" width="100%" alt="Fling в Windows, в браузере, в KDE Plasma и на Android">

<sub>Панель в Windows · расширение для браузера · KDE Plasma · Android</sub>

</div>

> **Авторство.** Это **неофициальный** порт **[Fling](https://github.com/tatianathevisionary/fling)** —
> приложения для macOS, созданного **[Damion Rashford](https://github.com/damionrashford)** (автор кода)
> и опубликованного **[Tatiana](https://github.com/tatianathevisionary)**. Идея, дизайн, тексты,
> каталог приложений и работа с протоколами принадлежат им. Этот репозиторий не их проект и не
> связан с ними; он только переносит их приложение на Windows, Linux, Android и в браузер.
> Код написан заново (Python/Qt, Java, JavaScript), Swift-код не копировался. Пока у оригинала
> нет лицензии, проект остаётся приватным. Подробнее — [CREDITS.md](CREDITS.md).

---

## Быстрый старт

| У меня… | Что сделать |
|---|---|
| **Windows 10 / 11** | Скачайте `Fling-Windows-….zip` со страницы [Releases](../../releases), распакуйте, дважды щёлкните **Install shortcuts.cmd**. Подробно: **[гайд для Windows](docs/install-windows.ru.md)** |
| **Chrome / Edge / Firefox / Safari** | Сначала поставьте приложение, затем [расширение](#расширение-для-браузера) |
| **Linux (KDE)** | `git clone` и `linux/install.sh` — [подробнее](#linux) |
| **Телефон на Android** | Поставьте `Fling.apk` со страницы [Releases](../../releases) — [подробнее](#android) |

Компьютер / телефон и телевизор должны быть в одной сети.

**Проверено на:** Windows 11 с Edge и Chrome, Kubuntu 26.04 (KDE Plasma 6), Android 17,
телевизор с Google TV. Расширение для Firefox и Safari собрано, но ещё не проверялось —
будем рады отзывам.

## Возможности

| | Windows | Linux (KDE) | Android | Расширение |
|---|:---:|:---:|:---:|:---:|
| Панель в стиле оригинального Fling | ✓ | ✓ | ✓ | ✓ окошко |
| Отправить текущую вкладку | ✓ **Win+Shift+C** | ✓ **Meta+Shift+C** | — | ✓ **Alt+Shift+C** |
| Продолжить на ТВ с того же места | ✓ ¹ | ✓ ¹ | — | ✓ |
| Ссылка из буфера / «Поделиться» / меню правой кнопки | ✓ | ✓ | ✓ | ✓ |
| Пауза, ±30 с, громкость, стоп | ✓ | ✓ | ✓ | ✓ |
| **Настоящее «что играет»** (название, обложка, время) через ADB | ✓ | ✓ | ✓ | ✓ |
| Уведомление о смене видео / трека (отключается) | ✓ | ✓ | — | — |
| YouTube «Связать с ТВ по коду» | ✓ | ✓ | — | — |
| Пульт: питание, приложения, стрелки, громкость, ввод текста | ✓ | ✓ | ✓ | ✓ |
| Голосовой поиск через микрофон | ✓ | ✓ | ✓ | — |
| Ноутбук как **Bluetooth-пульт** | — ² | ✓ | — | — |
| Окно «Подключение ТВ» в один клик | ✓ | ✓ | — | — |
| Стрелки колёсиком мыши или свайпом | ✓ | ✓ | ✓ | ✓ |
| Английский / русский интерфейс | ✓ | ✓ | ✓ | ✓ |
| Снимок экрана ТВ через ADB | ✓ | ✓ | ✓ | — |
| Установка APK на ТВ через ADB (выбрать файл или правый клик по нему в Проводнике) | ✓ | ✓ | — | — |

<sub>¹ когда вкладку отправляет расширение. ² Windows не позволяет программам быть Bluetooth-клавиатурой / пультом.</sub>

## Windows

**→ Подробный пошаговый гайд: [docs/install-windows.ru.md](docs/install-windows.ru.md).**

- **Готовая сборка:** `Fling-Windows-….zip` со страницы [Releases](../../releases) → распаковать в
  постоянную папку → **`Install shortcuts.cmd`**. Python ставить не нужно. SmartScreen может
  предупредить о неподписанной программе: *Подробнее → Выполнить в любом случае*.
- **Из исходников:** **`windows\install.cmd`** (удаление — `windows\uninstall.cmd`).

При первом запуске разрешите Fling доступ к **частным сетям** в окне брандмауэра.

## Расширение для браузера

Одно расширение для **Chrome, Edge, Яндекс Браузера, Brave, Opera, Vivaldi, Firefox и Safari**:
окошко с кнопкой «Отправить эту вкладку» (или **«Продолжить на ТВ с 12:34»**, если видео уже идёт),
что играет, пауза/перемотка, громкость и полный пульт; пункты в меню правой кнопки; **Alt+Shift+C**.
Расширение общается **только** с приложением Fling на этом же компьютере (127.0.0.1).

| Браузер | Установка |
|---|---|
| Chrome, Edge, Яндекс, Brave, Opera, Vivaldi | страница расширений → «Режим разработчика» → **«Загрузить распакованное»** → папка `extension\chrome` |
| Firefox | `about:debugging` → **«Загрузить временное дополнение»** → `fling-firefox-….zip` |
| Safari (Mac) | `extension/safari/build-safari.sh` на Mac с Xcode; нужен Fling на этом же Mac (пока не проверялось) |

Подробно — в [гайде, часть 2](docs/install-windows.ru.md#часть-2-расширение-для-браузера).

## Linux

```sh
git clone https://github.com/orfixe/fling-port.git && cd fling-port/linux
./install.sh
fling
```

Проверено на Kubuntu 26.04 (KDE Plasma 6). Удаление: `./install.sh --uninstall`.
Все команды и подробности — в [английском README](README.md#install--linux).

## Android

Android 8.0+. Скачайте `Fling.apk` со страницы [Releases](../../releases) и откройте на телефоне
(разрешите установку из этого источника), или соберите: `cd android && ./build.sh`.

## Подключение ТВ

**«Подключение ТВ…»** → **«Найти и подключить всё»**: поиск ТВ в сети, пульт по коду с экрана,
ADB («Разрешить отладку?» → «Всегда»), по желанию YouTube по коду и Bluetooth-пульт (Linux).
Подробно — в [гайде](docs/install-windows.ru.md#подключить-пульт-один-раз) и
[английском README](README.md#connecting-the-tv).

## Приватность

Всё происходит в вашей сети. Fling обращается только к телевизору и к youtube.com (названия и
обложки). Расширение передаёт адрес вкладки только приложению Fling на этом же компьютере и никогда
— из приватных окон. Ни аналитики, ни аккаунтов.

## Документация

| Документ | О чём |
|---|---|
| [Гайд для Windows](docs/install-windows.ru.md) · [EN](docs/install-windows.md) | Приложение, расширение, первый запуск, проблемы |
| [Architecture](docs/architecture.md) · [Protocols](docs/protocols.md) · [Development](docs/development.md) | Для разработчиков (англ.) |
| [Changelog](CHANGELOG.md) | Изменения по версиям |

## Лицензия

Код этого репозитория — [MIT](LICENSE). Оригинальный Fling — его дизайн, тексты и идея — принадлежит
Damion Rashford и Tatiana, см. [CREDITS.md](CREDITS.md).
