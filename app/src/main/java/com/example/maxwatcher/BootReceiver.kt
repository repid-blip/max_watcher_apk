package com.example.maxwatcher

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        Prefs.load(ctx)
        val i = Intent(ctx, MaxWatcherService::class.java)
        if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i)
        else ctx.startService(i)
    }
}