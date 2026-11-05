package com.example.maxwatcher

import android.content.Context

object Prefs {
    private const val NAME = "max_watcher_prefs"

    fun load(ctx: Context) {
        val p = ctx.getSharedPreferences(NAME, Context.MODE_PRIVATE)
        Config.sendButtonSelector = p.getString("send_button_selector", null)
    }

    fun save(ctx: Context) {
        ctx.getSharedPreferences(NAME, Context.MODE_PRIVATE).edit().apply {
            putString("send_button_selector", Config.sendButtonSelector)
            apply()
        }
    }
}