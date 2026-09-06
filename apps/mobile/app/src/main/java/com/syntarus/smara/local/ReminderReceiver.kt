package com.syntarus.smara.local

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat

class ReminderReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val channel = "smara_reminders"
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(channel, "Smara reminders", NotificationManager.IMPORTANCE_HIGH))
        manager.notify(System.currentTimeMillis().toInt(), NotificationCompat.Builder(context, channel)
            .setSmallIcon(com.syntarus.smara.R.drawable.ic_smara)
            .setContentTitle("Smara reminder")
            .setContentText(intent.getStringExtra(MESSAGE) ?: "You asked Smara to remind you.")
            .setAutoCancel(true).build())
    }
    companion object { const val MESSAGE = "message" }
}
