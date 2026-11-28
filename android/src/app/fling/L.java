// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.content.Context;

import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

/** Tiny localization: Russian strings are the keys, English is looked up.
 *  Language pref "lang" in prefs "fling": "ru" | "en" | "auto" (default). */
final class L {
    private L() {}

    private static boolean en;
    private static final Map<String, String> EN = new HashMap<>();

    static void init(Context ctx) {
        Diag.init(ctx);                                    // every screen, service and receiver calls init first
        String pref = ctx.getSharedPreferences("fling", Context.MODE_PRIVATE).getString("lang", "auto");
        if ("en".equals(pref)) en = true;
        else if ("ru".equals(pref)) en = false;
        else en = !"ru".equals(Locale.getDefault().getLanguage());
    }

    static boolean english() { return en; }

    static String lang(Context ctx) {
        return ctx.getSharedPreferences("fling", Context.MODE_PRIVATE).getString("lang", "auto");
    }

    static void setLang(Context ctx, String lang) {
        ctx.getSharedPreferences("fling", Context.MODE_PRIVATE).edit().putString("lang", lang).apply();
        init(ctx);
    }

    static String t(String ru) {
        if (!en) return ru;
        String s = EN.get(ru);
        return s != null ? s : ru;
    }

    static String f(String ru, Object... args) { return String.format(t(ru), args); }

    private static void e(String ru, String en) { EN.put(ru, en); }

    static {
        // first-run tips (Tour)
        e("Пропустить", "Skip");
        // messages (MainActivity.notice)
        e("Ошибка", "Error");
        e("Проверяю связь с ТВ…", "Checking the connection to the TV…");
        // updates (Updater)
        e("Обновление не скачалось", "The update didn't download");
        e("Повторить", "Try again");
        e("Скачать в браузере", "Download in the browser");
        e("Обновление уже скачивается — ход загрузки в уведомлении.", "The update is already downloading — see the notification.");
        e("У вас %s. Обновление скачается в фоне — ход загрузки будет в уведомлении, Fling можно закрыть. Связь с ТВ и настройки сохранятся.",
          "You have %s. The update downloads in the background — the progress is in a notification, and you can close Fling. Pairing and settings are kept.");
        e("Чтобы Fling мог поставить своё обновление, разрешите ему «Установку неизвестных приложений». Когда вернётесь, Fling сразу предложит обновиться.",
          "To install its update, Fling needs “Install unknown apps”. Allow it — when you come back, Fling offers the update right away.");
        e("Скачиваю обновление в фоне — ход загрузки в уведомлении.", "Downloading the update in the background — see the notification.");
        e("установка отменена.", "the installation was cancelled.");
        e("Обновления Fling", "Fling updates");
        e("Новая версия вышла, скачана или не скачалась.", "A new version is out, downloaded, or didn't download.");
        e("Загрузка обновления", "Update download");
        e("Ход загрузки новой версии.", "The progress of a new version's download.");
        e("Доступна версия Fling %s", "Fling %s is available");
        e("У вас %s. Нажмите «Обновить» — скачается в фоне.", "You have %s. Tap Update — it downloads in the background.");
        e("Обновление скачано", "Update downloaded");
        e("Fling %s скачан", "Fling %s is downloaded");
        e("Нажмите, чтобы установить.", "Tap to install.");
        e("Подключаюсь к GitHub…", "Connecting to GitHub…");
        e("Обновление Fling", "Fling update");
        e("Обновление Fling %s", "Fling %s update");
        e("Сообщать о новых версиях", "Tell me about new versions");
        e("Пропустить эту версию", "Skip this version");
        e("Отчёт для разработчика", "Report for the developer");
        e("Журнал работы, сведения о телефоне и настройки без паролей и токенов — приложите к сообщению об ошибке.",
          "The log, the phone and the settings without passwords or tokens — attach it to a bug report.");
        e("Отправить отчёт", "Send the report");
        e("Отчёт не получился: ", "The report failed: ");
        e("Устанавливать обновления автоматически", "Install updates automatically");
        e("Новая версия скачивается и ставится сама, пока Fling закрыт. Android может один раз попросить подтверждение.",
          "A new version downloads and installs by itself while Fling is closed. Android may ask to confirm once.");
        e("Раз в день Fling проверяет GitHub, даже закрытый, и присылает уведомление, когда вышла новая версия.",
          "Once a day Fling checks GitHub, even when closed, and sends a notification when a new version is out.");
        e("Связь оборвалась, повторяю (%d из %d)…", "The connection dropped, trying again (%d of %d)…");
        e("Не найден сервер %s: нет интернета или DNS не отвечает.", "Can't find the server %s: no internet, or DNS isn't answering.");
        e("Сервер %s не ответил вовремя. Попробуйте ещё раз или в другой сети.", "The server %s didn't answer in time. Try again, or on another network.");
        e("Соединение с %s обрывается — так бывает, когда сеть или провайдер его блокирует. Попробуйте другую сеть (Wi‑Fi ↔ мобильный интернет) или VPN, либо скачайте в браузере.",
          "The connection to %s keeps dropping — this happens when the network or the provider blocks it. Try another network (Wi‑Fi ↔ mobile data) or a VPN, or download in the browser.");
        // Smart View for Samsung (Samsung.java)
        e("Зеркалировать экран", "Mirror screen");
        e("Включить ТВ по сети", "Turn the TV on over the network");
        e("Выберите телевизор в списке «Smart View»", "Choose the TV in the Smart View list");
        e("На этом телефоне нет экрана трансляции. Откройте Smart View в шторке быстрых настроек.", "This phone has no cast screen. Open Smart View from the quick settings panel.");
        e("Голосовой поиск на Samsung не поддерживается — напечатайте запрос.", "Voice search doesn't work on Samsung — type your query.");
        e("Нажмите «Разрешить» на экране ТВ", "Press Allow on the TV screen");
        e("Не знаю MAC-адрес ТВ — включите его один раз пультом, чтобы Fling его запомнил.", "Fling doesn't know the TV's MAC address yet — turn the TV on once with its remote so Fling can remember it.");
        e("По адресу %s нет Chromecast, Google TV или Samsung.", "There's no Chromecast, Google TV or Samsung at %s.");
        e("Связь с ТВ прервалась.", "The connection to the TV was lost.");
        e("ТВ не ответил на запрос разрешения.", "The TV didn't answer the permission request.");
        e("ТВ не отвечает", "The TV doesn't answer");
        e("ТВ не отвечает — он выключен или в другой сети.", "The TV doesn't answer — it's off or on another network.");
        e("ТВ не поддерживает DLNA или он выключен.", "The TV doesn't support DLNA or it's off.");
        e("ТВ не принял ссылку: %s", "The TV didn't accept the link: %s");
        e("ТВ не принял файл: %s", "The TV didn't accept the file: %s");
        e("ТВ не разрешил подключение", "The TV refused the connection");
        e("ТВ не разрешил подключение. На ТВ: Общие → Диспетчер внешних устройств → Диспетчер подключения устройств — разрешите Fling.", "The TV refused the connection. On the TV: General → External Device Manager → Device Connection Manager — allow Fling.");
        e("Этого приложения нет на Samsung или Fling не знает, как его открыть.", "This app isn't on the Samsung, or Fling doesn't know how to open it.");
        // Settings → About
        e("Показать подсказки снова", "Show the tips again");
        e("Сборка", "Build");
        e("Далее", "Next");
        e("Готово", "Done");
        e("Полная инструкция", "Full guide");
        e("Ваш телевизор", "Your TV");
        e("Fling сам находит ТВ в той же сети Wi‑Fi. «Сменить» — другой ТВ или добавить по IP.", "Fling finds the TV on the same Wi‑Fi by itself. “Switch” picks another TV or adds one by IP.");
        e("Нажмите, и на ТВ появится код из 6 символов. После этого работают пауза, приложения и стрелки.", "Tap it and the TV shows a 6‑character code. Then pause, apps and the d‑pad work.");
        e("Пульт", "Remote");
        e("Приложения, стрелки или тачпад, громкость, текст и голос. Кнопки громкости телефона тоже управляют ТВ.", "Apps, d‑pad or touchpad, volume, typing and voice. The phone's volume keys control the TV too.");
        e("Отправить видео", "Cast a video");
        e("В YouTube или браузере: «Поделиться» → «На телевизор». Или скопируйте ссылку и вставьте её здесь.", "In YouTube or a browser: Share → “Cast to TV”. Or copy a link and paste it here.");
        e("Что играет", "Now playing");
        e("Пауза, ±30 секунд и громкость того, что идёт на ТВ.", "Pause, ±30 seconds and volume for whatever is playing on the TV.");
        e("Язык", "Language");
        e("Русский или английский — переключается здесь.", "Russian or English — switch it here.");
        e("Подсказки", "Tips");
        e("Эти подсказки всегда можно повторить кнопкой «?».", "Replay these tips any time with the “?” button.");
        e("Как пользоваться", "How to use");
        e("Понятно", "Got it");
        e("пульт ✓", "remote ✓");
        e("по IP", "by IP");
        e("ТВ не найдены", "No TVs found");
        e("Найти ТВ заново", "Search for TVs again");
        e("Добавить ТВ по IP…", "Add TV by IP…");
        e("Убрать добавленный вручную", "Remove the TV added by IP");
        e("Адрес телевизора", "The TV's address");
        e("В локальной сети — IP из настроек ТВ (Сеть). Через Tailscale — 100.x.y.z или имя MagicDNS.", "On the local network: the IP from the TV's settings (Network). Over Tailscale: 100.x.y.z or a MagicDNS name.");
        e("Добавить", "Add");
        e("Это не похоже на адрес ТВ (пример: 192.168.1.50 или 100.101.102.103).", "That doesn't look like a TV address (e.g. 192.168.1.50 or 100.101.102.103).");
        // remote access (Tailscale) and the path chip
        e("Удалённый доступ…", "Remote access…");
        e("Удалённый доступ", "Remote access");
        e("Чтобы управлять ТВ не из дома, поставьте Tailscale на телефон и на ТВ. На ТВ его ставит Fling для ПК — из «Каталога».", "To control the TV away from home, install Tailscale on the phone and on the TV. Fling for PC installs it on the TV from its Catalog.");
        e("Установить Tailscale", "Install Tailscale");
        e("Tailscale включён. Добавьте ТВ по его адресу 100.x.y.z или имени MagicDNS.", "Tailscale is on. Add the TV by its 100.x.y.z address or MagicDNS name.");
        e("Tailscale выключен. Включите его, чтобы управлять ТВ не из дома.", "Tailscale is off. Turn it on to control the TV away from home.");
        e("Добавить ТВ…", "Add a TV…");
        e("Открыть Tailscale", "Open Tailscale");
        e("Связь с ТВ есть", "Connected to the TV");
        e("Связь с ТВ через Tailscale", "Connected to the TV via Tailscale");
        e("Связь с ТВ через Tailscale · %d мс", "Connected to the TV via Tailscale · %d ms");
        e("Нет связи с ТВ", "No connection to the TV");
        e("Экран ТВ на телефоне", "TV screen on the phone");
        e("Экран ТВ на телефоне: смотрите и управляйте — дома и через Tailscale.", "The TV screen on your phone: watch and control it, at home or over Tailscale.");
        e("По адресу %s нет Chromecast / Google TV.", "No Chromecast / Google TV at %s.");
        e("Снимок экрана ТВ", "TV screenshot");
        e("Сделать снимок", "Take screenshot");
        e("Делаю снимок экрана ТВ…", "Taking a TV screenshot…");
        e("Сохранено в Pictures/Fling", "Saved to Pictures/Fling");
        e("Снимок чёрный: видео защищено от снимков (DRM у YouTube/Netflix). Меню и главный экран ТВ снимаются нормально.", "The screenshot is black: the video is protected (DRM in YouTube/Netflix). Menus and the home screen capture fine.");
        e("Снимок экрана: подтвердите на ТВ «Разрешить отладку».", "Screenshot: accept “Allow debugging” on the TV.");
        e("Снимок экрана работает через ADB — включите на ТВ «Для разработчиков» → «Отладка по сети».", "Screenshots need ADB — on the TV enable Developer options → Network debugging.");
        e("Снимок экрана не получился: ", "Screenshot failed: ");
        e("Оригинальный Fling создали Damion Rashford и Tatiana · неофициальный порт — orfixe", "Original Fling by Damion Rashford & Tatiana · unofficial port by orfixe");
        e("%s на ТВ", "%s on the TV");
        e("ТВ не сообщает, что сейчас играет — показываю только приложение.", "The TV doesn't report what's playing — showing the app only.");
        // errors: Cast / Atv
        e("Устройство не выбрано", "No device selected");
        e("Телевизор не ответил.", "The TV didn't respond.");
        e("Не удалось связаться с ТВ. Он включён и в той же сети Wi‑Fi или Tailscale?", "Couldn't reach the TV. Is it on, and on the same Wi‑Fi or Tailscale network?");
        e("Соединение с ТВ потеряно.", "Lost the connection to the TV.");
        e("Нет соединения с ТВ", "Not connected to the TV");
        e("Телевизор не смог открыть это видео.", "The TV couldn't open this video.");
        e("Телевизор не запустил проигрыватель (%s).", "The TV didn't start the player (%s).");
        e("На ТВ сейчас ничего не играет.", "Nothing is playing on the TV.");
        e("Телевизор не запустил проигрыватель вовремя.", "The TV didn't start the player in time.");
        e("Сначала нажмите «Связать с ТВ»", "Tap “Pair with TV” first");
        e("Код не совпадает — посмотрите на экран ТВ ещё раз.", "That code doesn't match — check the TV screen again.");
        e("Телевизор закрыл соединение.", "The TV closed the connection.");
        e("Телевизор отклонил код (статус %s). Начните заново.", "The TV rejected the code (status %s). Start over.");
        e("Этот ТВ не принимает голос — напечатайте запрос.", "This TV doesn't take voice input — type your search instead.");
        e("Телевизор не открыл голосовой поиск.", "The TV didn't open voice search.");
        e("Код должен состоять из 6 символов", "The code must be 6 characters");
        e("Код состоит из цифр 0–9 и букв A–F", "The code uses digits 0–9 and letters A–F");
        e("Телевизор больше не доверяет этому телефону — свяжите его с ТВ заново.", "The TV no longer trusts this phone — pair with the TV again.");
        // main screen
        e("В отправленном нет ссылки.", "There's no link in what was shared.");
        e("Ищу телевизор…", "Looking for the TV…");
        e("Чтобы открыть YouTube на ТВ, один раз нажмите «Связать с ТВ» ниже.", "To open YouTube on the TV, tap “Pair with TV” below once.");
        e("Эту страницу можно открыть на ТВ после того, как вы нажмёте «Связать с ТВ» ниже.", "This page opens on the TV once you tap “Pair with TV” below.");
        e("Отправлено на ТВ", "Sent to the TV");
        e("Указать IP вручную…", "Enter IP manually…");
        e("Искать заново", "Search again");
        e("Ищу ТВ…", "Looking for TVs…");
        e("Подключить", "Connect");
        e("Отмена", "Cancel");
        e("Телевизор игнорирует паузу по Chromecast (так ведёт себя YouTube на ТВ). Нажмите «Связать с ТВ» ниже — пауза будет работать как кнопка пульта.",
          "The TV ignores pause over Chromecast (YouTube on the TV does that). Tap “Pair with TV” below — pause is sent as a remote button.");
        e("Перемотка этого видео работает только после «Связать с ТВ» — кнопка ниже.", "To skip around in this video, tap “Pair with TV” below.");
        e("Без доступа к микрофону голосовой поиск не работает.", "Voice search needs microphone access.");
        e("Микрофон недоступен.", "The microphone is unavailable.");
        e("Остановить трансляцию", "Stop casting");
        e("Ссылка", "Link");
        e("Поделитесь ссылкой из YouTube или браузера → «На телевизор» — и видео сразу начнётся на ТВ. Или скопируйте ссылку и вставьте её здесь.",
          "Share a link from YouTube or a browser → “Cast to TV”, and it plays on the TV. Or copy a link and paste it here.");
        e("Отправить на ТВ", "Cast to TV");
        e("Вставить ссылку из буфера", "Paste copied link");
        e("В буфере обмена нет ссылки.", "There's no link on the clipboard.");
        e("Связываюсь с ТВ…", "Contacting the TV…");
        e("Введите код с экрана телевизора", "Enter the code from the TV screen");
        e("Связать", "Pair");
        e("Телевизор", "TV");
        e("Один раз свяжите телефон с ТВ, чтобы открыть приложения, стрелки, ввод текста, голосовой поиск и отправку YouTube-ссылок.",
          "Pair once with the TV to unlock apps, the d-pad, typing, voice search and sending YouTube links.");
        e("Связать с ТВ", "Pair with TV");
        // Updater
        e("Версия %s · Проверить обновления", "Version %s · Check for updates");
        e("Версия %s", "Version %s");
        e("У вас последняя версия (%s).", "You have the latest version (%s).");
        e("Не удалось проверить обновления: ", "Couldn't check for updates: ");
        e("В версии %s нет файла Fling.apk.", "Version %s has no Fling.apk file.");
        e("Доступна версия %s", "Version %s is available");
        e("У вас %s. Скачать и установить обновление? Связь с ТВ и настройки сохранятся.", "You have %s. Download and install the update? TV pairing and settings are kept.");
        e("Позже", "Later");
        e("Токен не подошёл", "The token didn't work");
        e("Нужен токен GitHub", "A GitHub token is needed");
        e("Репозиторий Fling приватный, поэтому GitHub отдаёт обновления только по токену. github.com → Settings → Developer settings → Fine-grained tokens → Generate: Repository access — только fling-port, Permissions → Contents — Read-only. Токен хранится только на этом телефоне.", "The Fling repository is private, so GitHub hands out updates only with a token. github.com → Settings → Developer settings → Fine-grained tokens → Generate: Repository access — only fling-port, Permissions → Contents — Read-only. The token is kept on this phone only.");
        e("Сохранить и проверить", "Save and check");
        e("Открыть GitHub", "Open GitHub");
        e("Разрешите установку", "Allow installation");
        e("Чтобы Fling мог поставить своё обновление, разрешите ему «Установку неизвестных приложений» и нажмите «Обновить» ещё раз.", "To let Fling install its update, allow it to “Install unknown apps”, then tap “Update” again.");
        e("Открыть настройки", "Open settings");
        e("Обновление до %s", "Updating to %s");
        e("Скачиваю…", "Downloading…");
        e("Скачиваю… %d%%", "Downloading… %d%%");
        e("Открываю установку…", "Opening the installer…");
        e("Обновление не скачалось: ", "The update didn't download: ");
        e("файл скачался не полностью", "the file didn't download completely");
        e("подпись нового APK не совпадает с установленным — удалите старый Fling и поставьте новый вручную.", "the new APK's signature doesn't match the installed one — uninstall the old Fling and install the new one manually.");
        e("Обновление не установилось: ", "The update didn't install: ");
        e("Телевизор ещё не найден.", "No TV found yet.");
        e("На телевизоре", "On the TV");
        e("Кнопки", "Buttons");
        e("Тачпад", "Touchpad");
        e("Громк −", "Vol −");
        e("Без звука", "Mute");
        e("Громк +", "Vol +");
        e("Кнопки громкости телефона тоже управляют ТВ", "The phone's volume buttons control the TV too");
        e("Напечатать на ТВ", "Type on TV");
        e("Слушаю — нажмите микрофон ещё раз, когда закончите.", "Listening — tap the mic again when done.");
        e("Сменить", "Switch");
        e("Fling для Android · дома по Wi‑Fi, не из дома — через Tailscale", "Fling for Android · Wi‑Fi at home, Tailscale when away");
        e("Ищу телевизор в сети…", "Looking for a TV on the network…");
        e("Пульт для телевизора и трансляция с телефона", "TV remote and casting from your phone");
        e("Воспроизводится", "Playing");
        e("ТВ не принимает команды по Chromecast для этого видео. Нажмите «Связать с ТВ» ниже — пауза и перемотка будут работать как кнопки пульта.",
          "The TV ignores Chromecast commands for this video. Tap “Pair with TV” below — pause and skip are sent as remote buttons.");
        e("видеофайл · Chromecast", "video file · Chromecast");
        e("откроется в YouTube на ТВ", "opens in YouTube on the TV");
        e("сначала «Связать с ТВ»", "pair with TV first");
        e("откроется на ТВ", "opens on the TV");
        e("Отправить вместо текущего", "Cast this instead");
        e("Недавние", "Recent");
        e("Сейчас: %s", "Now: %s");
        e("ТВ выключен", "TV is off");
        e("Подключаюсь…", "Connecting…");
        e("Главный экран", "Home screen");
        e("Устройство не найдено", "No device found");
        e("Пауза на ТВ", "Pause on TV");
        e("ТВ", "TV");
        e("Откройте Fling", "Open Fling");
        e("Обновить", "Update");
        e("Обновить данные", "Refresh");
        e("Свайп — навигация · тап — OK", "Swipe to move · tap for OK");
        e("долгое нажатие — назад", "long press for Back");
        e("Язык", "Language");

        // 0.4: phone files, playback notification, sleep timer, settings
        e("Сейчас на ТВ", "Now on TV");
        e("Не удалось открыть файл.", "Couldn't open the file.");
        e("Пропущено файлов: %d — %s", "Files skipped: %d — %s");
        e("Назад на 30 с", "Back 30 s");
        e("Вперёд на 30 с", "Forward 30 s");
        e("Закрыть", "Close");
        e("На «%s»", "On “%s”");
        e("На телевизоре", "On the TV");
        e("Предыдущий", "Previous");
        e("Следующий", "Next");
        e("Пауза", "Pause");
        e("Воспроизвести", "Play");
        e("Отправляю на ТВ…", "Sending to the TV…");
        e("Отправляю файлы на ТВ (%d)…", "Sending %d files to the TV…");
        e("На телефоне нет приложения для выбора файлов.", "This phone has no app for picking files.");
        e("Файл с телефона", "File from this phone");
        e("Этот файл не видео, не музыка и не фото.", "This file isn't a video, music or a photo.");
        e("Телефон не в той же сети, что и ТВ.", "The phone isn't on the same network as the TV.");
        e("Не удалось прочитать фото.", "Couldn't read the photo.");
        e("Таймер сна", "Sleep timer");
        e("Выключить таймер", "Cancel the timer");
        e("Таймер сна выключен", "Sleep timer cancelled");
        e("ТВ выключится в %s", "The TV will turn off at %s");
        e("Через %d мин", "In %d min");
        e("Через 1 час", "In 1 hour");
        e("Через 1,5 часа", "In 1.5 hours");
        e("Через %d часа", "In %d hours");
        e("в %s", "at %s");
        e("выкл.", "off");
        e("Настройки", "Settings");
        e("Тема, цвет акцента, язык, уведомление с кнопками и кнопки громкости.", "Theme, accent colour, language, the playback notification and the volume keys.");
        e("Назад", "Back");
        e("Оформление", "Appearance");
        e("Как в системе", "System default");
        e("Светлая", "Light");
        e("Тёмная", "Dark");
        e("Авто", "Auto");
        e("Чёрный фон вместо тёмно-серого. На экранах OLED и AMOLED бережёт заряд.", "Black instead of dark grey. Saves battery on OLED and AMOLED screens.");
        e("Пример видео", "Sample video");
        e("Так будет выглядеть Fling", "This is how Fling will look");
        e("Цвет акцента", "Accent colour");
        e("Цвета обоев (Material You)", "Wallpaper colours (Material You)");
        e("Цвета обоев (Material You) доступны с Android 12.", "Wallpaper colours (Material You) need Android 12 or later.");
        e("Синий", "Blue");
        e("Индиго", "Indigo");
        e("Фиолетовый", "Purple");
        e("Розовый", "Pink");
        e("Бирюзовый", "Teal");
        e("Зелёный", "Green");
        e("Графит", "Graphite");
        e("Пульт и трансляция", "Remote and casting");
        e("Уведомление с кнопками", "Playback notification");
        e("Пока на ТВ что-то играет: пауза, перемотка и громкость с экрана блокировки.", "While something plays on the TV: pause, skip and volume from the lock screen.");
        e("Кнопки громкости управляют ТВ", "Volume keys control the TV");
        e("Пока Fling открыт, кнопки громкости телефона меняют громкость ТВ.", "While Fling is open, the phone's volume keys change the TV volume.");
        e("Не гасить экран", "Keep the screen on");
        e("Экран телефона не гаснет, пока открыт Fling.", "The phone screen stays on while Fling is open.");
        e("О приложении", "About");
        e("Проверить обновления", "Check for updates");

        // TV chooser (DevicePicker)
        e("GitHub ограничил число проверок с этого адреса. Попробуйте через час.", "GitHub limited checks from this address. Try again in an hour.");
        e("ADB · отладка по сети", "ADB · network debugging");
        e("LIVE и ADB, без пульта и Cast", "LIVE and ADB, no remote or Cast");
        e("Tailscale включён", "Tailscale is on");
        e("Tailscale выключен", "Tailscale is off");
        e("Tailscale не установлен", "Tailscale isn't installed");
        e("Wi‑Fi, ADB и Tailscale · обычно до 10 секунд.", "Wi‑Fi, ADB and Tailscale · usually up to 10 seconds.");
        e("В сети", "Online");
        e("Включите его, чтобы ТВ из tailnet были на связи.", "Turn it on to reach the TVs on your tailnet.");
        e("Добавить по адресу", "Add by address");
        e("Дома · Wi‑Fi", "At home · Wi‑Fi");
        e("Искать снова", "Search again");
        e("Ищу телевизоры…", "Looking for TVs…");
        e("Нажмите на ТВ, чтобы переключиться.", "Tap a TV to switch to it.");
        e("Найдено: %d", "Found: %d");
        e("Не в сети", "Offline");
        e("Нужен на телефоне и на ТВ, чтобы управлять ТВ не из дома.", "Needed on the phone and the TV to control the TV away from home.");
        e("Открыть", "Open");
        e("Проверьте, что ТВ включён и в той же сети Wi‑Fi, что и телефон. Если ТВ в другой подсети, добавьте его по адресу ниже.", "Make sure the TV is on and on the same Wi‑Fi as the phone. If it's on another subnet, add it by its address below.");
        e("Проверяю %s…", "Checking %s…");
        e("Сейчас выбран", "Selected");
        e("ТВ из tailnet добавляется один раз — по адресу ниже.", "Add a tailnet TV once, by its address below.");
        e("Телевизоры", "TVs");
        e("Телевизоры не найдены", "No TVs found");
        e("Убрать этот ТВ из списка", "Remove this TV from the list");
        e("Установить", "Install");
        e("Устройство с ADB", "ADB device");
        e("добавлен по IP", "added by IP");
        e("пульт", "remote");

        // Cast device authentication (CastAuth)
        e("Устройство не подтвердило, что это настоящий Chromecast, поэтому Fling не стал с ним соединяться.", "The device didn't prove it is a genuine Chromecast, so Fling didn't connect to it.");

        // LIVE: scrcpy session (Scrcpy)
        e("Файл scrcpy-server не совпал с контрольной суммой, поэтому он не запущен.", "The scrcpy-server file didn't match its checksum, so it wasn't started.");
        e("Подтвердите на ТВ «Разрешить отладку» и нажмите LIVE ещё раз.", "Accept “Allow debugging” on the TV, then tap LIVE again.");
        e("LIVE работает через ADB — включите на ТВ «Для разработчиков» → «Отладка по сети».", "LIVE works over ADB — on the TV, turn on Developer options → Network debugging.");
        e("ТВ отключил видео.", "The TV turned the video off.");
        e("ТВ не смог настроить видео.", "The TV couldn't set up the video.");
        e("ТВ не запустил scrcpy: %s", "The TV didn't start scrcpy: %s");
        e("Не удалось скачать scrcpy-server: ", "Couldn't download scrcpy-server: ");

        // LIVE: screen and mini remote (LiveActivity)
        e("Подключение…", "Connecting…");
        e("Переподключение…", "Reconnecting…");
        e("Снижаю качество…", "Lowering the quality…");
        e("Повторить", "Retry");
        e("Видео с ТВ прервалось.", "The picture from the TV stopped.");
        e("Телефон не может показать видео с этого ТВ.", "This phone can't play the video from this TV.");
        e("Касание — OK, свайп — стрелки, удержание — «Назад»", "Tap for OK, swipe for the arrows, press and hold for Back");
        e("Домой", "Home");
        e("Вверх", "Up");
        e("Вниз", "Down");
        e("Влево", "Left");
        e("Вправо", "Right");
        e("Тише", "Volume down");
        e("Громче", "Volume up");
        e("Пауза / воспроизведение", "Play/pause");
        e("Клавиатура", "Keyboard");
        e("%d мс", "%d ms");
        e("мс", "ms");
    }
}
