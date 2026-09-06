package com.syntarus.smara.local

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.pdf.PdfRenderer
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.provider.OpenableColumns
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.devanagari.DevanagariTextRecognizerOptions
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import kotlinx.coroutines.suspendCancellableCoroutine
import java.io.ByteArrayOutputStream
import java.io.File
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlin.math.max

data class OcrDocument(
    val text: String,
    val fileName: String,
    val mimeType: String,
    val bytes: ByteArray,
    val pagesRead: Int,
    val truncated: Boolean = false,
) {
    val isUseful: Boolean get() = DocumentOcrEngine.isUsefulText(text)
}

/**
 * Privacy-first document reader.
 *
 * Born-digital PDFs take the cheap native path. Scans and photos are rendered
 * and read with bundled ML Kit models. The caller may send a weak result to
 * Smara's existing Gemini document OCR route as a quality fallback.
 */
class DocumentOcrEngine(private val context: Context) {
    suspend fun read(uri: Uri, maxLocalPdfPages: Int = 12): OcrDocument {
        val resolver = context.contentResolver
        val bytes = resolver.openInputStream(uri)?.use { it.readBytes() }
            ?: error("The selected file could not be opened.")
        val name = displayName(uri) ?: "document"
        val mime = resolver.getType(uri) ?: mimeFromName(name)
        if (mime == "application/pdf" || name.endsWith(".pdf", ignoreCase = true)) {
            val native = PhoneTools.extractPdfText(context, uri)
            if (isUsefulText(native)) return OcrDocument(native, name, "application/pdf", bytes, 0)
            return readPdf(uri, name, bytes, maxLocalPdfPages)
        }
        if (mime.startsWith("image/")) {
            val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                ?: error("This image format could not be decoded.")
            return OcrDocument(recognize(bitmap), name, mime, bytes, 1)
        }
        // Word/Excel/PowerPoint and other supported formats are intentionally
        // handed to the cloud converter instead of pretending OCR can parse them.
        return OcrDocument("", name, mime, bytes, 0)
    }

    suspend fun readCamera(bitmap: Bitmap): OcrDocument {
        val output = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, 92, output)
        return OcrDocument(recognize(bitmap), "scan-${System.currentTimeMillis()}.jpg", "image/jpeg", output.toByteArray(), 1)
    }

    private suspend fun readPdf(uri: Uri, name: String, bytes: ByteArray, cap: Int): OcrDocument {
        // Cloud/document providers often expose a pipe rather than a seekable
        // descriptor. PdfRenderer rejects those intermittently. Copy to the
        // app cache first so every provider has the same reliable path.
        val cached = File.createTempFile("smara-ocr-", ".pdf", context.cacheDir)
        cached.outputStream().use { it.write(bytes) }
        try {
            ParcelFileDescriptor.open(cached, ParcelFileDescriptor.MODE_READ_ONLY).use { fd ->
                PdfRenderer(fd).use { renderer ->
                val limit = minOf(renderer.pageCount, cap)
                val result = StringBuilder()
                for (index in 0 until limit) {
                    renderer.openPage(index).use { page ->
                        val targetWidth = 1800
                        val targetHeight = max(1, targetWidth * page.height / max(1, page.width))
                        val bitmap = Bitmap.createBitmap(targetWidth, targetHeight, Bitmap.Config.ARGB_8888)
                        page.render(bitmap, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
                        val pageText = recognize(bitmap)
                        bitmap.recycle()
                        if (pageText.isNotBlank()) result.append("=== PAGE ${index + 1} ===\n").append(pageText).append("\n\n")
                    }
                }
                    return OcrDocument(result.toString().trim(), name, "application/pdf", bytes, limit, renderer.pageCount > limit)
                }
            }
        } finally {
            cached.delete()
        }
    }

    private suspend fun recognize(bitmap: Bitmap): String {
        val image = InputImage.fromBitmap(bitmap, 0)
        val latin = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
        val devanagari = TextRecognition.getClient(DevanagariTextRecognizerOptions.Builder().build())
        return try {
            val latinText = latin.process(image).await().text.trim()
            val devanagariText = devanagari.process(image).await().text.trim()
            // Running both scripts avoids a language switch in the UI. Choose
            // one coherent transcription instead of concatenating duplicates.
            if (quality(devanagariText) > quality(latinText)) devanagariText else latinText
        } finally {
            latin.close()
            devanagari.close()
        }
    }

    private fun displayName(uri: Uri): String? = context.contentResolver.query(
        uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null,
    )?.use { cursor -> if (cursor.moveToFirst()) cursor.getString(0) else null }

    companion object {
        // Indic scripts use combining marks heavily, so a Latin-sized raw
        // letter threshold would wrongly escalate perfectly readable Hindi.
        fun isUsefulText(text: String): Boolean = quality(text) >= 45 && text.count { it.isLetterOrDigit() } >= 25
        private fun quality(text: String): Int = text.count { it.isLetterOrDigit() } + text.lines().count { it.isNotBlank() } * 3
        private fun mimeFromName(name: String): String = when (name.substringAfterLast('.', "").lowercase()) {
            "pdf" -> "application/pdf"
            "png" -> "image/png"
            "webp" -> "image/webp"
            "jpg", "jpeg" -> "image/jpeg"
            "docx" -> "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            "xlsx" -> "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            "pptx" -> "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            else -> "application/octet-stream"
        }
    }
}

private suspend fun <T> com.google.android.gms.tasks.Task<T>.await(): T = suspendCancellableCoroutine { continuation ->
    addOnSuccessListener { if (continuation.isActive) continuation.resume(it) }
    addOnFailureListener { if (continuation.isActive) continuation.resumeWithException(it) }
    addOnCanceledListener { continuation.cancel() }
}
