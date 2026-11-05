package com.example.maxwatcher

object Config {
    // ---------- MAX ----------
    const val MAX_URL = "https://web.max.ru/"
    const val DESKTOP_UA =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    const val VIEWPORT_W = 1400
    const val VIEWPORT_H = 900

    // ---------- Watcher ----------
    const val POLL_INTERVAL_MS   = 2_000L
    const val COOLDOWN_MS        = 5_000L
    const val PAGE_SETTLE_MS     = 1_500L
    const val LOAD_WAIT_MS       = 8_000L
    const val CHAT_LIST_TIMEOUT  = 60_000L
    const val MISSED_SETTLE_MS   = 4_000L
    const val RELOAD_INTERVAL_MS = 90_000L
    const val MAX_FILES_CHECK    = 30
    const val MAX_IMAGES_CHECK   = 12
    const val SEND_CHECK_MS      = 1_500L
    const val REPLY_TIMEOUT_MS   = 30_000L

    // ---------- Images ----------
    const val DOWNLOAD_IMAGES  = true
    const val IMAGE_MIN_WIDTH  = 120
    const val IMAGE_MIN_HEIGHT = 80

    // ---------- Notifications ----------
    const val CH_STATUS   = "mw_status"
    const val CH_STATUS_N = "MAX Watcher: работает"
    const val CH_NEWS     = "mw_news"
    const val CH_NEWS_N   = "MAX Watcher: события"

    // ---------- Настройки пользователя ----------
    /** CSS-селектор кнопки «Отправить» в MAX. Если null — ищем типовые и жмём Enter. */
    var sendButtonSelector: String? = null
}