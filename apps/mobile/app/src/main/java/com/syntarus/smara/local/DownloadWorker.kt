package com.syntarus.smara.local

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import androidx.core.app.NotificationCompat
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.io.OutputStream

class DownloadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val url = inputData.getString(URL) ?: return Result.failure()
        val name = inputData.getString(FILE_NAME)?.replace(Regex("[^A-Za-z0-9._-]"), "_") ?: "smara-download"
        val channel = ensureChannel(applicationContext)
        val notification = NotificationCompat.Builder(applicationContext, channel)
            .setSmallIcon(com.syntarus.smara.R.drawable.ic_smara)
            .setContentTitle("Smara download")
            .setContentText(name)
            .setOngoing(true)
            .setProgress(100, 0, true)
            .build()
        val manager = applicationContext.getSystemService(NotificationManager::class.java)
        manager.notify(id.hashCode(), notification)
        return try {
            val connection = URL(url).openConnection() as HttpURLConnection
            connection.connectTimeout = 15_000
            connection.readTimeout = 30_000
            connection.connect()
            if (connection.responseCode !in 200..299) return Result.retry()
            val total = connection.contentLengthLong
            val output: OutputStream
            val mediaUri = if (Build.VERSION.SDK_INT >= 29) {
                applicationContext.contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, android.content.ContentValues().apply {
                    put(MediaStore.Downloads.DISPLAY_NAME, name)
                    put(MediaStore.Downloads.MIME_TYPE, "application/octet-stream")
                    put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/Smara")
                })
            } else null
            output = if (mediaUri != null) requireNotNull(applicationContext.contentResolver.openOutputStream(mediaUri))
            else File(applicationContext.getExternalFilesDir("downloads"), name).outputStream()
            connection.inputStream.use { input -> output.use { outputStream ->
                val buffer = ByteArray(16 * 1024); var done = 0L; var read: Int
                while (input.read(buffer).also { read = it } != -1) {
                    outputStream.write(buffer, 0, read); done += read
                    if (total > 0) setProgress(androidx.work.Data.Builder().putInt("percent", (done * 100 / total).toInt()).build())
                }
            }}
            manager.notify(id.hashCode(), NotificationCompat.Builder(applicationContext, channel)
                .setSmallIcon(com.syntarus.smara.R.drawable.ic_smara).setContentTitle("Download ready")
                .setContentText(name).setAutoCancel(true).build())
            Result.success()
        } catch (_: Exception) {
            manager.cancel(id.hashCode()); Result.retry()
        }
    }

    private fun ensureChannel(context: Context): String {
        val id = "smara_tasks"
        if (Build.VERSION.SDK_INT >= 26) context.getSystemService(NotificationManager::class.java)
            .createNotificationChannel(NotificationChannel(id, "Smara tasks", NotificationManager.IMPORTANCE_LOW))
        return id
    }

    companion object { const val URL = "url"; const val FILE_NAME = "file_name" }
}
