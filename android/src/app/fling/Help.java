// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.app.Activity;
import android.app.AlertDialog;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/** "How to use" guide: sections of title + bullet steps, in Russian and English. */
final class Help {
    private Help() {}

    /** Russian sections: {title, body}. Body lines start with "• ". */
    private static final String[][] RU = {
            {"Первый запуск", "• Подключите телефон к той же сети Wi‑Fi, что и телевизор.\n• Откройте Fling — он сам найдёт ТВ, имя будет внизу экрана.\n• Один раз нажмите «Связать с ТВ» и включите отладку ADB (раздел «Название и обложка с ТВ»).\n• Эта инструкция всегда открывается кнопкой «?» вверху."},
            {"Выбор телевизора", "• Телефон и ТВ должны быть в одной сети Wi‑Fi.\n• Fling сам найдёт ТВ — имя будет внизу экрана.\n• «Сменить» — другой телевизор. «Найти ТВ заново» — поискать ещё раз.\n• Чтобы добавить ТВ самому, нажмите «Добавить ТВ по IP…». Чтобы убрать его — «Убрать добавленный вручную»."},
            {"Удалённый доступ (Tailscale)", "• Поставьте Tailscale на телефон и на ТВ и войдите в одну сеть (tailnet). На ТВ его ставит Fling для ПК — из «Каталога».\n• «Сменить» → «Добавить ТВ по IP…»: адрес ТВ в Tailscale (100.x.y.z) или имя MagicDNS. Рядом с ним будет метка Tailscale.\n• Если ТВ уже связан с телефоном дома, связывать заново не нужно.\n• Под названием вверху видно, как идёт связь: «Дома» или «Через Tailscale» и задержка в миллисекундах.\n• Пульт, LIVE и отладка ADB работают через Tailscale. ТВ не находится сам — его адрес нужно добавить один раз."},
            {"Связать с ТВ", "• В карточке «Телевизор» нажмите «Связать с ТВ».\n• На ТВ появится код из 6 символов: цифры и буквы A–F.\n• Введите его в приложении.\n• После этого заработают пауза, кнопки приложений, стрелки, ввод текста, голос и отправка ссылок YouTube."},
            {"Отправить видео на ТВ", "• В YouTube или браузере: «Поделиться» → «На телевизор».\n• Или скопируйте ссылку и в карточке «Ссылка» нажмите «Вставить ссылку из буфера», затем «Отправить на ТВ».\n• Последние 5 ссылок можно отправить ещё раз.\n• Ссылки YouTube открываются в приложении YouTube на ТВ — сначала нажмите «Связать с ТВ».\n• Прямые ссылки на файлы (mp4, mkv, mp3, m3u8…) играют и без этого.\n• Другие сайты открываются на ТВ, если вы уже нажали «Связать с ТВ»."},
            {"Фото, видео и музыка с телефона", "• В «Галерее» или файловом менеджере: «Поделиться» → «На телевизор». Можно выбрать сразу несколько файлов — они пойдут по очереди.\n• Или в карточке «Ссылка» нажмите «Файл с телефона».\n• Файлы идут на ТВ прямо с телефона по Wi‑Fi, поэтому не выключайте Wi‑Fi, пока они играют.\n• Фото поворачиваются как надо и уменьшаются под экран ТВ.\n• Связывать с ТВ для этого не нужно."},
            {"Что играет сейчас", "• Карточка вверху показывает обложку, название и время.\n• Пауза, перемотка на 30 секунд, громкость и «Остановить трансляцию».\n• Нажмите ⟳ на обложке, чтобы обновить."},
            {"Название и обложка с ТВ", "• Чтобы видеть настоящее название, исполнителя и обложку того, что играет на ТВ, нужна отладка ADB.\n• На ТВ: Настройки → Система → О системе → 7 раз нажмите «Сборка».\n• Затем: Настройки → Система → Для разработчиков → включите отладку по сети.\n• Когда ТВ спросит «Разрешить отладку?» — нажмите «Разрешить». Лучше с галочкой «Всегда»."},
            {"Пульт", "• Кнопки приложений: YouTube, Netflix и другие.\n• Переключатель «Кнопки» / «Тачпад».\n• Тачпад: провести пальцем — движение по меню, коснуться — OK, долгое нажатие — Назад.\n• Кнопки громкости.\n• Поле, чтобы напечатать текст на ТВ.\n• Микрофон — голосовой поиск. Приложение попросит доступ.\n• Кнопка питания."},
            {"Кнопки громкости, уведомление и плитка", "• Пока Fling открыт, громкость телефона меняет громкость ТВ.\n• Пока на ТВ что-то играет, в шторке и на экране блокировки есть уведомление с кнопками: пауза, перемотка, следующий файл, стоп.\n• «Пауза на ТВ» в шторке: откройте шторку → карандаш → перетащите плитку «Пауза на ТВ»."},
            {"Таймер сна", "• В пульте нажмите «Таймер сна» и выберите, через сколько выключить ТВ: от 15 минут до 2 часов.\n• Таймер сработает, даже если Fling закрыт, а телефон заблокирован.\n• Уже выключенный ТВ таймер не включит."},
            {"Настройки", "• Шестерёнка вверху экрана.\n• Тема: как в системе, светлая, тёмная или чёрная (AMOLED).\n• Цвет акцента, на Android 12 и новее — цвета обоев (Material You).\n• Уведомление с кнопками, кнопки громкости, «Не гасить экран» и язык."},
            {"Снимок экрана ТВ", "• «Сделать снимок» снимает экран телевизора.\n• Файл попадёт в «Галерею», папка Pictures/Fling.\n• Если снимок чёрный, видео защищено (так бывает у YouTube и Netflix). Меню и главный экран получаются нормально.\n• Чтобы снимок получился, включите отладку ADB."},
            {"LIVE: экран ТВ на телефоне", "• «● LIVE» вверху показывает экран телевизора на телефоне и даёт управлять им — дома и через Tailscale.\n• Нужна отладка ADB (раздел «Название и обложка с ТВ»).\n• В первый раз Fling скачает scrcpy-server с GitHub и проверит его контрольную сумму."},
            {"Если что-то не работает", "• ТВ не находится — проверьте, что телефон и ТВ в одной сети Wi‑Fi, или нажмите «Добавить ТВ по IP…».\n• Пауза на YouTube не работает — нажмите «Связать с ТВ».\n• Старая или чужая обложка — включите отладку ADB.\n• Чёрный снимок — видео защищено.\n• Язык: переключатель RU | EN внизу экрана."}
    };

    /** English sections, same order as RU. */
    private static final String[][] EN = {
            {"First launch", "• Put the phone on the same Wi‑Fi as the TV.\n• Open Fling. It finds the TV by itself; the name shows at the bottom.\n• Tap “Pair with TV” once, and turn on ADB debugging (“Title and artwork from the TV”).\n• This guide is always under the “?” button at the top."},
            {"Choose a TV", "• The phone and the TV need to be on the same Wi‑Fi.\n• Fling finds the TV by itself; its name shows at the bottom.\n• “Switch” picks another TV. “Search for TVs again” looks once more.\n• To add one yourself, tap “Add TV by IP…”. To remove it, tap “Remove the TV added by IP”."},
            {"Remote access (Tailscale)", "• Install Tailscale on the phone and on the TV and sign both in to the same tailnet. Fling for PC installs it on the TV from its Catalog.\n• “Switch” → “Add TV by IP…”: the TV’s Tailscale address (100.x.y.z) or its MagicDNS name. It gets a Tailscale label.\n• A TV already paired with the phone at home doesn’t need pairing again.\n• Under the title at the top you see the path: “At home”, or “Via Tailscale” with the delay in milliseconds.\n• The remote, LIVE and ADB work over Tailscale. The TV isn’t found by itself there: add its address once."},
            {"Pair with TV", "• In the “TV” card, tap “Pair with TV”.\n• The TV shows a 6-character code: digits and the letters A–F.\n• Type it in the app.\n• After that, pause, app buttons, the d-pad, typing, voice and YouTube links work."},
            {"Cast a video", "• In YouTube or a browser: Share → “Cast to TV”.\n• Or copy a link, then in the “Link” card tap “Paste copied link”, then “Cast to TV”.\n• The last 5 links can be sent again.\n• YouTube links open in the YouTube app on the TV — tap “Pair with TV” first.\n• Direct file links (mp4, mkv, mp3, m3u8…) play without pairing.\n• Other sites open on the TV after you tap “Pair with TV”."},
            {"Photos, videos and music from the phone", "• In Gallery or a file manager: Share → “Cast to TV”. Pick several files and they play one after another.\n• Or in the “Link” card, tap “File from this phone”.\n• The files go to the TV straight from the phone over Wi‑Fi, so keep Wi‑Fi on while they play.\n• Photos are turned the right way up and sized for the TV.\n• No pairing is needed for this."},
            {"What’s playing", "• The top card shows the artwork, the title and the time.\n• Pause, skip 30 seconds, volume, and “Stop casting”.\n• Tap ⟳ on the artwork to refresh."},
            {"Title and artwork from the TV", "• To see the real title, artist and artwork of whatever is on the TV, turn on ADB debugging.\n• On the TV: Settings → System → About → tap “Build” 7 times.\n• Then: Settings → System → Developer options → turn on network debugging.\n• When the TV asks “Allow debugging?”, tap “Allow”. “Always” is the one you want."},
            {"Remote", "• App buttons: YouTube, Netflix and the rest.\n• “Buttons” or “Touchpad”.\n• Touchpad: swipe to move, tap for OK, long-press for Back.\n• Volume buttons.\n• A field to type on the TV.\n• Mic — voice search. The app will ask for the microphone.\n• Power."},
            {"Volume keys, notification and tile", "• While Fling is open, the phone’s volume keys change the TV volume.\n• While something plays on the TV, a notification with buttons sits in the shade and on the lock screen: pause, skip, next file, stop.\n• To add “Pause on TV”: open quick settings → the pencil → drag the “Pause on TV” tile in."},
            {"Sleep timer", "• In the remote, tap “Sleep timer” and choose when to turn the TV off: from 15 minutes to 2 hours.\n• It works even with Fling closed and the phone locked.\n• A TV that is already off stays off."},
            {"Settings", "• The gear at the top of the screen.\n• Theme: system default, light, dark or black (AMOLED).\n• Accent colour; on Android 12 and later, wallpaper colours (Material You).\n• Playback notification, volume keys, “Keep the screen on” and language."},
            {"Screenshot", "• “Take screenshot” captures the TV screen.\n• It is saved in Gallery, folder Pictures/Fling.\n• A black picture means the video is copy-protected (YouTube and Netflix do this). Menus and the home screen come out fine.\n• To get a real screenshot, turn on ADB debugging."},
            {"LIVE: the TV screen on the phone", "• “● LIVE” at the top shows the TV screen on the phone and lets you control it — at home and over Tailscale.\n• It needs ADB debugging (“Title and artwork from the TV”).\n• The first time, Fling downloads scrcpy-server from GitHub and checks its checksum."},
            {"If something doesn’t work", "• TV not found — check that the phone and the TV are on the same Wi‑Fi, or tap “Add TV by IP…”.\n• Pause on YouTube does nothing — tap “Pair with TV”.\n• Wrong or old artwork — turn on ADB debugging.\n• Black screenshot — the video is copy-protected.\n• Language: the RU | EN switch at the bottom."}
    };

    /** Shows the guide as a scrollable dialog styled like the rest of the app. */
    static void show(Activity a, Ui ui) {
        String[][] secs = L.english() ? EN : RU;               // follows the app language switch
        LinearLayout col = ui.col();
        col.setPadding(ui.dp(18), ui.dp(6), ui.dp(18), ui.dp(12));
        for (String[] s : secs) {
            TextView t = ui.text(s[0], 17, ui.fg, true);        // section title
            t.setPadding(0, ui.dp(14), 0, ui.dp(4));
            col.addView(t);
            TextView b = ui.text(s[1], 15, ui.fgA(0.75f), false); // bullet steps
            b.setLineSpacing(ui.dp(3), 1f);
            col.addView(b);
        }
        ScrollView sv = new ScrollView(a);                      // long text: make it scroll
        sv.addView(col);
        new AlertDialog.Builder(a)
                .setTitle(L.t("Как пользоваться"))
                .setView(sv)
                .setPositiveButton(L.t("Понятно"), null)
                .show();
    }
}
