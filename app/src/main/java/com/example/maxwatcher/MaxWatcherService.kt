package com.example.maxwatcher

import android.annotation.SuppressLint
import android.app.Service
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Canvas
import android.os.Build
import android.os.IBinder
import android.util.Log
import android.view.View
import android.webkit.*
import kotlinx.coroutines.*
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.*
import kotlin.coroutines.resume
import kotlin.coroutines.suspendCoroutine
import kotlin.math.max

class MaxWatcherService : Service() {

    companion object {
        const val TAG = "MaxWatcher"
        var isRunning = false; private set
        private const val NOTIF_ID = 1001
    }

    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private lateinit var webView: WebView
    private var lastCounts: MutableMap<String, Int> = mutableMapOf()
    private var seenFiles: MutableMap<String, MutableSet<String>> = mutableMapOf()
    private var seenImages: MutableMap<String, MutableSet<String>> = mutableMapOf()

    private val screenshotsDir: File by lazy {
        File(getExternalFilesDir(null), "screenshots").apply { mkdirs() }
    }
    private val downloadsDir: File by lazy {
        File(getExternalFilesDir(null), "downloads").apply { mkdirs() }
    }

    override fun onCreate() {
        super.onCreate()
        Prefs.load(this)
        isRunning = true
        Notifier.ensureChannels(this)
        startForeground(NOTIF_ID, Notifier.foregroundNotification(this, "Запуск…"))
        setupWebView()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == "STOP") { stopSelf(); return START_NOT_STICKY }
        startWatcher()
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        isRunning = false
        scope.cancel()
        try { webView.destroy() } catch (_: Exception) {}
        super.onDestroy()
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        webView = WebView(applicationContext)
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            userAgentString = Config.DESKTOP_UA
            useWideViewPort = true
            loadWithOverviewMode = true
        }
        CookieManager.getInstance().apply {
            setAcceptCookie(true)
            setAcceptThirdPartyCookies(webView, true)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.KITKAT) {
            WebView.setWebContentsDebuggingEnabled(true)
        }
        webView.measure(
            View.MeasureSpec.makeMeasureSpec(Config.VIEWPORT_W, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(Config.VIEWPORT_H, View.MeasureSpec.EXACTLY)
        )
        webView.layout(0, 0, Config.VIEWPORT_W, Config.VIEWPORT_H)

        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                Log.i(TAG, "Страница загружена: $url")
            }
        }
        webView.webChromeClient = WebChromeClient()

        webView.setDownloadListener { url, _, contentDisposition, mimetype, _ ->
            val name = contentDisposition
                ?.substringAfter("filename=", "download_${System.currentTimeMillis()}")
                ?.trim('"') ?: "download_${System.currentTimeMillis()}"
            Log.i(TAG, "DownloadListener: $url → $name (mime=$mimetype)")
            try {
                val dm = getSystemService(Context.DOWNLOAD_SERVICE) as android.app.DownloadManager
                dm.enqueue(android.app.DownloadManager.Request(android.net.Uri.parse(url))
                        .setTitle(name)
                        .setNotificationVisibility(
                            android.app.DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                        .setDestinationInExternalFilesDir(this@MaxWatcherService, null, "downloads/$name")
                )
            } catch (e: Exception) {
                Log.w(TAG, "DownloadManager не принял URL: ${e.message}")
            }
        }

        webView.loadUrl(Config.MAX_URL)
    }

    private fun startWatcher() {
        scope.launch {
            delay(Config.LOAD_WAIT_MS)
            Log.i(TAG, "Watcher: старт слежения")
            var nextReload = System.currentTimeMillis() + Config.RELOAD_INTERVAL_MS

            while (isActive) {
                try {
                    if (System.currentTimeMillis() >= nextReload) {
                        webView.reload()
                        delay(Config.LOAD_WAIT_MS)
                        nextReload = System.currentTimeMillis() + Config.RELOAD_INTERVAL_MS
                    }

                    val counts = getUnreadCounts()
                    if (counts.isNotEmpty()) {
                        Log.i(TAG, "Непрочитанные: $counts")
                        for ((chat, cnt) in counts) {
                            val prev = lastCounts[chat] ?: 0
                            if (cnt > prev) {
                                Log.i(TAG, "Новое в чате «$chat»: $prev → $cnt")
                                processChat(chat, cnt)
                            }
                        }
                    }
                    lastCounts = counts
                } catch (e: CancellationException) {
                    break
                } catch (e: Exception) {
                    Log.e(TAG, "Watcher tick: ${e.message}", e)
                }
                delay(Config.POLL_INTERVAL_MS)
            }
        }
    }

    private suspend fun getUnreadCounts(): MutableMap<String, Int> {
        val js = WatcherJs.chatsJs(".badgeIcon:has(.text)", 120, "", emptyList())
        val raw = evalJs(js)
        val unquoted = unquote(raw) ?: return mutableMapOf()
        return try {
            val o = JSONObject(unquoted)
            val m = mutableMapOf<String, Int>()
            for (k in o.keys()) m[k] = o.getInt(k)
            m
        } catch (e: Exception) {
            Log.w(TAG, "counts parse: ${e.message}")
            mutableMapOf()
        }
    }

    private suspend fun processChat(chat: String, newMessages: Int) {
        val openRes = evalJs(WatcherJs.openChatJs(chat, Config.PAGE_SETTLE_MS))
        if (!openRes.contains("\"ok\":true")) {
            Log.w(TAG, "Не удалось открыть чат «$chat»: $openRes")
            return
        }
        delay(Config.PAGE_SETTLE_MS)

        // ---- Файлы ----
        val filesJson = evalJs(WatcherJs.fileButtonsJs(".info--interactive", "Скачать"))
        val files = parseFilesList(filesJson)
        val toCheck = minOf(max(newMessages, 1), Config.MAX_FILES_CHECK, files.size)
        val seen = seenFiles.getOrPut(chat) { mutableSetOf() }
        val newFiles = files.takeLast(toCheck).filter { !seen.contains(it.fp()) }
        for (f in newFiles) {
            evalJs(WatcherJs.clickDownloadJs(".info--interactive", "Скачать", f.i))
            seen.add(f.fp())
            delay(1500)
        }

        // ---- Картинки ----
        val newImages = if (Config.DOWNLOAD_IMAGES) {
            val imgsJson = evalJs(WatcherJs.imagesJs(Config.IMAGE_MIN_WIDTH, Config.IMAGE_MIN_HEIGHT))
            val imgs = parseImagesList(imgsJson)
            val seenImgs = seenImages.getOrPut(chat) { mutableSetOf() }
            val fresh = imgs.takeLast(Config.MAX_IMAGES_CHECK).filter { !seenImgs.contains(it.key) }
            for (img in fresh) {
                downloadImage(chat, img)
                seenImgs.add(img.key)
            }
            fresh
        } else emptyList()

        // ---- Скриншот, если есть текст без вложений ----
        val hasOnlyText = newMessages > (newFiles.size + newImages.size)
        val screenshot = if (hasOnlyText) takeScreenshot(chat) else null

        // ---- Уведомление ----
        val body = buildString {
            append("Чат: ").append(chat)
            if (newFiles.isNotEmpty()) append("\nФайлов: ").append(newFiles.size)
            if (newImages.isNotEmpty()) append("\nКартинок: ").append(newImages.size)
            if (screenshot != null) append("\n(есть текст — скриншот сохранён)")
            append("\n\nОткройте приложение, чтобы ответить.")
        }
        Notifier.news(this, "MAX: новое в «$chat»", body)
        Log.i(TAG, "«$chat»: файлов=${newFiles.size}, картинок=${newImages.size}, screenshot=${screenshot?.name ?: "нет"}")
    }

    private suspend fun downloadImage(chat: String, img: ImageInfo) {
        val reqId = UUID.randomUUID().toString()
        val result = suspendCoroutine<Pair<String, String>?> { cont ->
            val bridge = object {
                @JavascriptInterface fun imageData(id: String, b64: String, ext: String) {
                    if (id == reqId) cont.resume(b64 to ext)
                }
                @JavascriptInterface fun imageError(id: String, err: String) {
                    if (id == reqId) { Log.w(TAG, "img fetch: $err"); cont.resume(null) }
                }
            }
            webView.addJavascriptInterface(bridge, "ImgBridge_$reqId")
            webView.post { webView.evaluateJavascript(WatcherJs.fetchImageJs(reqId, img.src), null) }
        } ?: return
        val (b64, ext) = result
        val bytes = android.util.Base64.decode(b64, android.util.Base64.DEFAULT)
        val safe = chat.replace(Regex("""[\\/:*?"<>|]"""), "_")
        val name = "${safe}_${SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())}_img.$ext"
        File(downloadsDir, name).writeBytes(bytes)
        Log.i(TAG, "Картинка: $name")
    }

    private fun takeScreenshot(chat: String): File? = try {
        val bmp = Bitmap.createBitmap(webView.width, webView.height, Bitmap.Config.ARGB_8888)
        webView.draw(Canvas(bmp))
        val safe = chat.replace(Regex("""[\\/:*?"<>|]"""), "_")
        val f = File(screenshotsDir,
            "${safe}_${SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())}.png")
        f.outputStream().use { bmp.compress(Bitmap.CompressFormat.PNG, 100, it) }
        f
    } catch (e: Exception) {
        Log.e(TAG, "screenshot: ${e.message}"); null
    }

    private suspend fun evalJs(js: String): String = suspendCoroutine { cont ->
        webView.post { webView.evaluateJavascript(js) { r -> cont.resume(r ?: "") } }
    }

    private data class FileInfo(val i: Int, val text: String, val time: String) {
        fun fp(): String = "$text $time"
    }
    private data class ImageInfo(val idx: Int, val time: String, val ratio: String, val src: String) {
        val key: String get() = "img $ratio $time"
    }

    private fun parseFilesList(json: String): List<FileInfo> {
        val u = unquote(json) ?: return emptyList()
        return try {
            val arr = JSONArray(u)
            (0 until arr.length()).map {
                val o = arr.getJSONObject(it)
                FileInfo(o.getInt("i"), o.getString("text"), o.optString("time"))
            }
        } catch (e: Exception) { emptyList() }
    }

    private fun parseImagesList(json: String): List<ImageInfo> {
        val u = unquote(json) ?: return emptyList()
        return try {
            val arr = JSONArray(u)
            (0 until arr.length()).mapNotNull {
                val o = arr.getJSONObject(it)
                if (!o.optBoolean("loaded")) return@mapNotNull null
                ImageInfo(o.getInt("idx"), o.optString("time"), o.optString("ratio"), o.optString("src"))
            }
        } catch (e: Exception) { emptyList() }
    }

    private fun unquote(s: String): String? {
        if (s.isBlank() || s == "null") return null
        return if (s.startsWith("\"") && s.endsWith("\"")) {
            try { JSONObject("{\"v\":$s}").getString("v") } catch (_: Exception) { null }
        } else s
    }
}
