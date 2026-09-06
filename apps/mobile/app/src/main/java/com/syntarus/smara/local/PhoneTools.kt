package com.syntarus.smara.local

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.work.Data
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import java.io.BufferedReader
import java.io.InputStreamReader
import java.util.UUID
import java.util.regex.Pattern

object PhoneTools {
    fun enqueueDownload(context: Context, url: String, fileName: String): UUID {
        val input = Data.Builder().putString(DownloadWorker.URL, url).putString(DownloadWorker.FILE_NAME, fileName).build()
        val request = OneTimeWorkRequestBuilder<DownloadWorker>().setInputData(input).build()
        WorkManager.getInstance(context).enqueue(request)
        return request.id
    }

    /** Extracts plain text operators from text-based PDFs without uploading the file. */
    fun extractPdfText(context: Context, uri: Uri): String {
        val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() } ?: return ""
        val raw = bytes.toString(Charsets.ISO_8859_1)
        val matcher = Pattern.compile("\\(([^()]*)\\)\\s*T[Jj]").matcher(raw)
        val result = StringBuilder()
        while (matcher.find()) {
            if (result.isNotEmpty()) result.append(' ')
            result.append(matcher.group(1).orEmpty().replace("\\n", " ").replace("\\r", " "))
        }
        return result.toString().trim()
    }

    fun scheduleReminder(context: Context, message: String, atMillis: Long): Boolean {
        val alarm = context.getSystemService(AlarmManager::class.java) ?: return false
        val intent = Intent(context, ReminderReceiver::class.java).putExtra(ReminderReceiver.MESSAGE, message)
        val pending = PendingIntent.getBroadcast(context, message.hashCode(), intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        if (Build.VERSION.SDK_INT >= 31 && !alarm.canScheduleExactAlarms()) {
            alarm.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMillis, pending)
        } else {
            alarm.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMillis, pending)
        }
        return true
    }
}
