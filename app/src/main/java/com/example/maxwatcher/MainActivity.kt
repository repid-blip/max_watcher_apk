package com.example.maxwatcher

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.webkit.CookieManager
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.*
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import java.io.File

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var statusLabel: TextView
    private lateinit var startStopBtn: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Prefs.load(this)
        setContentView(R.layout.activity_main)

        webView      = findViewById(R.id.webView)
        statusLabel  = findViewById(R.id.statusLabel)
        startStopBtn = findViewById(R.id.startStopBtn)

        setupWebView()
        requestPerms()
        startStopBtn.setOnClickListener { toggleWatcher() }
        findViewById<Button>(R.id.settingsBtn).setOnClickListener { openSettings() }
        findViewById<Button>(R.id.filesBtn).setOnClickListener { openFiles() }
        findViewById<Button>(R.id.batteryBtn).setOnClickListener { requestIgnoreBattery() }

        updateStatus()
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            userAgentString = Config.DESKTOP_UA
        }
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true)
        webView.webViewClient = WebViewClient()
        webView.loadUrl(Config.MAX_URL)
    }

    private fun requestPerms() {
        val need = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            need += Manifest.permission.POST_NOTIFICATIONS
        }
        if (need.isNotEmpty()) ActivityCompat.requestPermissions(this, need.toTypedArray(), 1)
    }

    private fun toggleWatcher() {
        if (MaxWatcherService.isRunning) {
            startService(Intent(this, MaxWatcherService::class.java).setAction("STOP"))
        } else {
            val i = Intent(this, MaxWatcherService::class.java)
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(i) else startService(i)
        }
        webView.postDelayed({ updateStatus() }, 500)
    }

    private fun updateStatus() {
        val on = MaxWatcherService.isRunning
        statusLabel.text = if (on) "Статус: работает" else "Статус: остановлен"
        startStopBtn.text = if (on) "■ Остановить" else "▶ Запустить"
        statusLabel.postDelayed({ if (!isFinishing) updateStatus() }, 1500)
    }

    private fun openFiles() {
        val dir = File(getExternalFilesDir(null), "downloads")
        Toast.makeText(this, "Папка: ${dir.absolutePath}", Toast.LENGTH_LONG).show()
    }

    private fun requestIgnoreBattery() {
        if (Build.VERSION.SDK_INT < 23) return
        val pm = getSystemService(POWER_SERVICE) as PowerManager
        if (pm.isIgnoringBatteryOptimizations(packageName)) {
            Toast.makeText(this, "Уже разрешено", Toast.LENGTH_SHORT).show()
            return
        }
        @SuppressLint("BatteryLife")
        startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
            Uri.parse("package:$packageName")))
    }

    private fun openSettings() {
        val view = layoutInflater.inflate(R.layout.dialog_settings, null)
        val sel = view.findViewById<EditText>(R.id.sendSelector)
        sel.setText(Config.sendButtonSelector ?: "")

        AlertDialog.Builder(this)
            .setTitle("Настройки")
            .setView(view)
            .setPositiveButton("Сохранить") { _, _ ->
                Config.sendButtonSelector = sel.text.toString().trim().ifBlank { null }
                Prefs.save(this)
                Toast.makeText(this, "Сохранено", Toast.LENGTH_SHORT).show()
            }
            .setNegativeButton("Отмена", null)
            .show()
    }
}