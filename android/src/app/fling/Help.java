package app.fling;

import android.app.Activity;
import android.app.AlertDialog;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/** "How to use" guide: sections of title + bullet steps, in Russian and English.
 *  Drafted by a local LLM from a fact list, then checked and completed by hand. */
final class Help {
    private Help() {}

    /** Russian sections: {title, body}. Body lines start with "• ". */
    private static final String[][] RU = {
            {"Первый запуск", "• Подключите телефон к той же сети Wi‑Fi, что и телевизор.\n• Откройте Fling — он сам найдёт ТВ, его название появится внизу экрана.\n• Один раз свяжите пульт (раздел «Связать пульт») и включите отладку ADB (раздел «Название и обложка с ТВ»).\n• Эта инструкция всегда открывается кнопкой «?» вверху."},
            {"Выбор телевизора", "• Убедитесь, что телефон и ТВ находятся в одной сети Wi‑Fi.\n• Приложение автоматически ищет ТВ; выбранный ТВ отображается внизу экрана.\n• Нажмите «Сменить» для выбора другого ТВ или «Найти ТВ заново».\n• Для ручного добавления используйте «Добавить ТВ по IP…», а чтобы удалить – «Убрать добавленный вручную»."},
            {"Связать пульт", "• В карточке «Телевизор» нажмите «Связать пульт с ТВ…».\n• На ТВ появится код из 6 символов (цифры и буквы A–F).\n• Введите этот код в приложении.\n• После связывания работают пауза, кнопки приложений, крестовина, ввод текста, голос, отправка YouTube‑ссылок."},
            {"Отправить видео на ТВ", "• В YouTube или браузере нажмите «Поделиться» → «На телевизор».\n• Или скопируйте ссылку и в карточке «Ссылка» нажмите «Вставить ссылку из буфера», затем «Отправить на ТВ».\n• Последние 5 ссылок можно отправлять повторно.\n• YouTube‑ссылки открываются в приложении YouTube (требуется связанный пульт).\n• Прямые ссылки на файлы (mp4, mkv, mp3, m3u8…) работают без пульта.\n• Другие сайты открываются на ТВ, если пульт связан."},
            {"Что играет сейчас", "• Карточка вверху показывает обложку, название и время.\n• Кнопки пауза/играть, назад/вперёд 30 секунд, громкость, «Остановить трансляцию».\n• Нажмите ⟳ на обложке для обновления данных."},
            {"Название и обложка с ТВ", "• Чтобы видеть настоящее название, исполнителя и обложку того, что играет на ТВ, нужна отладка ADB.\n• На ТВ: Настройки → Система → О системе → 7 раз нажмите «Сборка».\n• Затем: Настройки → Система → Для разработчиков → включите отладку по сети.\n• Когда ТВ спросит «Разрешить отладку?» для этого телефона — нажмите «Разрешить» (лучше с «Всегда»)."},
            {"Пульт", "• Кнопки приложений (YouTube, Netflix и др.).\n• Переключатель «Кнопки» / «Тачпад».\n• Тачпад: провести пальцем – движение по меню; коснуться – OK; долгое нажатие – Назад.\n• Кнопки громкости.\n• Поле ввода текста.\n• Микрофон – голосовой поиск (необходимо разрешение).\n• Кнопка питания."},
            {"Кнопки громкости и плитка в шторке", "• Громкость телефона управляет громкостью ТВ, пока приложение открыто.\n• Чтобы добавить «Пауза на ТВ» в шторку быстрых настроек: открыть шторку → нажать карандаш (изменить) → перетащить плитку «Пауза на ТВ»."},
            {"Снимок экрана ТВ", "• Нажмите «Снимок экрана ТВ».\n• Сохранится в Галерея → Pictures/Fling.\n• Если снимок чёрный – видео защищено DRM (YouTube/Netflix); меню снимается нормально.\n• Для корректного снимка нужна отладка ADB."},
            {"Если что-то не работает", "• ТВ не находится – проверьте одну сеть Wi‑Fi или «Добавить ТВ по IP…».\n• Пауза на YouTube не работает – свяжите пульт.\n• Старая обложка – включите отладку ADB.\n• Чёрный снимок экрана – DRM.\n• Язык приложения: переключатель RU | EN внизу экрана."}
    };

    /** English sections, same order as RU. */
    private static final String[][] EN = {
            {"First launch", "• Connect the phone to the same Wi‑Fi network as the TV.\n• Open Fling — it finds the TV by itself; its name appears at the bottom.\n• Pair the remote once (“Set up remote”) and turn on ADB debugging (“Title and artwork from the TV”).\n• This guide always opens with the “?” button at the top."},
            {"Choose a television", "• Ensure your phone and TV are on the same Wi‑Fi network.\n• The app automatically searches for TVs; the selected TV is shown at the bottom of the screen.\n• Tap “Switch” to choose another TV or “Find TVs again”.\n• To add manually use “Add TV by IP…”, and to remove a manual entry tap “Remove a manually added TV”."},
            {"Set up remote", "• In the “Television” card tap “Pair TV remote…”.\n• A 6‑character code (digits and letters A–F) will appear on the TV.\n• Enter this code in the app.\n• After linking, pause, app buttons, exit, text input, voice, and sending YouTube links work."},
            {"Cast a video to TV", "• In YouTube or a browser tap “Share” → “To TV”.\n• Or copy a link and in the “Link” card tap “Paste copied link”, then “Cast to TV”.\n• The last 5 links can be cast again.\n• YouTube links open in the YouTube app on TV (requires linked remote).\n• Direct file links (mp4, mkv, mp3, m3u8…) play without a remote.\n• Other websites open on the TV when the remote is paired."},
            {"What’s playing now", "• The top card shows cover art, title and time.\n• Buttons for pause/play, rewind/forward 30 s, volume, “Stop casting”.\n• Tap ⟳ on the cover to refresh data."},
            {"Title and artwork from the TV", "• To see the real title, artist and artwork of what the TV is playing, turn on ADB debugging.\n• On the TV: Settings → System → About → tap “Build” 7 times.\n• Then: Settings → System → Developer options → turn on network debugging.\n• When the TV asks “Allow debugging?” for this phone, tap “Allow” (preferably “Always”)."},
            {"Remote", "• App buttons (YouTube, Netflix, etc.).\n• Switch between “Buttons” / “Touchpad”.\n• Touchpad: swipe – navigate menu; tap – OK; long‑press – Back.\n• Volume buttons.\n• Text input field.\n• Microphone – voice search (app will request microphone access).\n• Power button."},
            {"Volume buttons and quick‑settings tile", "• Phone volume controls TV volume while the app is open.\n• To add “Pause on TV” to the quick‑settings panel: open the panel → tap pencil (edit) → drag the “Pause on TV” tile."},
            {"TV screenshot", "• Tap “TV screenshot”.\n• It will be saved in Gallery → Pictures/Fling.\n• If the screenshot is black – video is DRM protected (YouTube/Netflix); menu screenshots work normally.\n• Proper screenshot requires ADB debugging."},
            {"If something doesn’t work", "• TV not found – check same Wi‑Fi network or “Add TV by IP…”.\n• Pause on YouTube not working – link the remote.\n• Outdated cover art – enable ADB debugging.\n• Black screenshot – DRM.\n• App language: the RU | EN switch at the bottom."}
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
