package com.syntarus.smara.local

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

data class LocalFileHit(val name: String, val uri: Uri, val mimeType: String?)

class FolderFileRepository(private val context: Context) {
    private val prefs = context.getSharedPreferences("smara_local_files", Context.MODE_PRIVATE)

    fun saveFolder(uri: Uri) {
        runCatching {
            context.contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
        }
        prefs.edit().putString(KEY_FOLDER, uri.toString()).apply()
    }

    fun hasFolder(): Boolean = prefs.contains(KEY_FOLDER)

    suspend fun search(hint: String, maxResults: Int = 8): List<LocalFileHit> = withContext(Dispatchers.IO) {
        val root = prefs.getString(KEY_FOLDER, null)?.let(Uri::parse) ?: return@withContext emptyList()
        // People describe a file conversationally ("can you open my resume
        // named sujal_kherawat...").  Search only meaningful filename tokens;
        // requiring the whole sentence was the reason valid files were missed.
        val query = hint.lowercase()
            .replace(Regex("[^a-z0-9]+"), " ")
            .split(Regex("\\s+"))
            .filter { it.length > 1 && it !in STOP_WORDS }
        val results = mutableListOf<LocalFileHit>()
        walk(root, DocumentsContract.getTreeDocumentId(root), query, results, maxResults, 0)
        results
    }

    fun open(hit: LocalFileHit) {
        context.startActivity(Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(hit.uri, hit.mimeType ?: "*/*")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        })
    }

    private fun walk(treeUri: Uri, parentDocumentId: String, query: List<String>, out: MutableList<LocalFileHit>, limit: Int, depth: Int) {
        if (out.size >= limit || depth > 5) return
        val children = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, parentDocumentId)
        val projection = arrayOf(DocumentsContract.Document.COLUMN_DOCUMENT_ID, DocumentsContract.Document.COLUMN_DISPLAY_NAME, DocumentsContract.Document.COLUMN_MIME_TYPE)
        context.contentResolver.query(children, projection, null, null, null)?.use { cursor ->
            val id = cursor.getColumnIndex(DocumentsContract.Document.COLUMN_DOCUMENT_ID)
            val name = cursor.getColumnIndex(DocumentsContract.Document.COLUMN_DISPLAY_NAME)
            val mime = cursor.getColumnIndex(DocumentsContract.Document.COLUMN_MIME_TYPE)
            while (cursor.moveToNext() && out.size < limit) {
                val childId = cursor.getString(id) ?: continue
                val display = cursor.getString(name) ?: ""
                val type = cursor.getString(mime)
                val childUri = DocumentsContract.buildDocumentUriUsingTree(treeUri, childId)
                if (type == DocumentsContract.Document.MIME_TYPE_DIR) walk(treeUri, childId, query, out, limit, depth + 1)
                else {
                    val normalizedName = display.lowercase().replace(Regex("[^a-z0-9]+"), " ")
                    val matches = query.isEmpty() || query.all(normalizedName::contains)
                    if (matches) out += LocalFileHit(display, childUri, type)
                }
            }
        }
    }

    companion object {
        private const val KEY_FOLDER = "approved_folder"
        private val STOP_WORDS = setOf(
            "a", "an", "and", "are", "can", "could", "find", "for", "from", "get",
            "give", "here", "i", "in", "is", "it", "kind", "me", "my", "named", "name",
            "of", "on", "open", "please", "show", "the", "this", "to", "u", "up", "where",
            "with", "you", "your",
        )
    }
}
