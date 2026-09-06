package com.syntarus.smara.local

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import androidx.core.app.NotificationCompat

class TaskNotifier(private val context: Context) {
    private val channel = "smara_agent"
    private val id = 4201

    fun show(message: String) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(channel, "Smara agent", NotificationManager.IMPORTANCE_LOW))
        manager.notify(id, NotificationCompat.Builder(context, channel)
            .setSmallIcon(com.syntarus.smara.R.drawable.ic_smara)
            .setContentTitle("Smara is working")
            .setContentText(message)
            .setOngoing(true)
            .setProgress(0, 0, true)
            .build())
    }

    fun complete(message: String) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.notify(id, NotificationCompat.Builder(context, channel)
            .setSmallIcon(com.syntarus.smara.R.drawable.ic_smara)
            .setContentTitle("Smara finished")
            .setContentText(message)
            .setAutoCancel(true)
            .build())
    }
}
