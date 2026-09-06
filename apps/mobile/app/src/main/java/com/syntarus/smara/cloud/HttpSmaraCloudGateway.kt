package com.syntarus.smara.cloud

import com.syntarus.smara.security.SecureTokenStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

class HttpSmaraCloudGateway(
    private val backendUrl: String,
    private val tokenStore: SecureTokenStore,
) : SmaraCloudGateway {
    override fun stream(request: CloudAgentRequest): Flow<CloudAgentEvent> = flow {
        val session = tokenStore.read()
            ?: throw CloudSessionException("Sign in to connect this phone to your Smara memory.")
        val connection = open("/v1/memento/chat", "POST", session).apply {
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("Accept", "text/event-stream")
            readTimeout = 0
            doOutput = true
            outputStream.bufferedWriter().use { writer ->
                writer.write(
                    JSONObject()
                        .put("message", request.message)
                        .put("conversation_id", request.conversationId)
                        .put("attachment_ids", org.json.JSONArray(request.attachmentIds))
                        .put("deep_reasoning", request.deepReasoning)
                        .toString(),
                )
            }
        }

        try {
            val code = connection.responseCode
            if (code == 401) {
                tokenStore.clear()
                throw CloudSessionException("Your phone session expired. Sign in again.")
            }
            if (code !in 200..299) {
                val body = connection.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                val detail = runCatching { JSONObject(body).optString("detail") }.getOrNull()
                error(detail?.takeIf { it.isNotBlank() } ?: "Smara could not start this task ($code).")
            }

            connection.inputStream.bufferedReader().use { reader ->
                while (true) {
                    val line = reader.readLine() ?: break
                    if (!line.startsWith("data:")) continue
                    val event = decode(line.removePrefix("data:").trim()) ?: continue
                    emit(event)
                }
            }
        } finally {
            connection.disconnect()
        }
    }.flowOn(Dispatchers.IO)

    override suspend fun upload(fileName: String, mimeType: String, bytes: ByteArray): CloudAttachment =
        withContext(Dispatchers.IO) {
            require(bytes.isNotEmpty()) { "The selected file is empty." }
            val session = tokenStore.read()
                ?: throw CloudSessionException("Sign in to use Smara cloud OCR for this document.")
            val boundary = "Smara-${UUID.randomUUID()}"
            val safeName = fileName.replace(Regex("[\\r\\n\\\"]"), "_")
            val connection = open("/v1/memento/upload", "POST", session).apply {
                setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
                setRequestProperty("Accept", "application/json")
                doOutput = true
            }
            try {
                connection.outputStream.buffered().use { output ->
                    output.write("--$boundary\r\n".toByteArray())
                    output.write("Content-Disposition: form-data; name=\"file\"; filename=\"$safeName\"\r\n".toByteArray())
                    output.write("Content-Type: $mimeType\r\n\r\n".toByteArray())
                    output.write(bytes)
                    output.write("\r\n--$boundary--\r\n".toByteArray())
                }
                val code = connection.responseCode
                if (code == 401) {
                    tokenStore.clear()
                    throw CloudSessionException("Your phone session expired. Sign in again.")
                }
                val body = (if (code in 200..299) connection.inputStream else connection.errorStream)
                    ?.bufferedReader()?.use { it.readText() }.orEmpty()
                if (code !in 200..299) {
                    val detail = runCatching { JSONObject(body).optString("detail") }.getOrNull()
                    error(detail?.takeIf { it.isNotBlank() } ?: "Smara could not read this file ($code).")
                }
                val json = JSONObject(body)
                CloudAttachment(
                    id = json.getString("attachment_id"),
                    fileName = json.optString("filename", fileName),
                    kind = json.optString("kind", "document"),
                    characters = json.optInt("chars", 0),
                )
            } finally {
                connection.disconnect()
            }
        }

    override suspend fun resolveApproval(confirmId: String, approved: Boolean) = withContext(Dispatchers.IO) {
        require(confirmId.matches(Regex("^[A-Za-z0-9_-]{1,128}$"))) { "Invalid confirmation ID." }
        val session = tokenStore.read() ?: throw CloudSessionException("Sign in again to approve this action.")
        val connection = open("/v1/memento/confirm/$confirmId", "POST", session).apply {
            setRequestProperty("Content-Type", "application/json")
            doOutput = true
            outputStream.bufferedWriter().use { it.write(JSONObject().put("approved", approved).toString()) }
        }
        try {
            val code = connection.responseCode
            if (code !in 200..299) {
                val body = connection.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                val detail = runCatching { JSONObject(body).optString("detail") }.getOrNull()
                error(detail?.takeIf { it.isNotBlank() } ?: "Could not send the approval ($code).")
            }
        } finally {
            connection.disconnect()
        }
    }

    private fun decode(data: String): CloudAgentEvent? {
        val json = runCatching { JSONObject(data) }.getOrNull() ?: return null
        return when (json.optString("type")) {
            "phase" -> CloudAgentEvent.Phase(json.optString("phase", "working"))
            "status" -> CloudAgentEvent.Status(json.optString("label", "Working"), json.nullable("detail"))
            "memory_search" -> CloudAgentEvent.MemorySearch(json.optString("query"), json.optInt("hits"))
            "tool_call" -> CloudAgentEvent.CapabilityStarted(json.optString("name", "tool"))
            "tool_result" -> CloudAgentEvent.CapabilityFinished(json.optString("name", "tool"), json.optBoolean("ok"))
            "token" -> CloudAgentEvent.Token(json.optString("text"))
            "stream_reset" -> CloudAgentEvent.StreamReset
            "confirm_request" -> CloudAgentEvent.ApprovalRequired(
                id = json.optString("confirm_id"),
                preview = json.optString("preview", "Approve this action?"),
            )
            "skill_proposal" -> CloudAgentEvent.SkillLearned(
                id = json.optString("skill_id"),
                name = json.optString("name", "Learned workflow"),
                description = json.optString("description"),
                tools = json.optJSONArray("tools")?.let { array ->
                    (0 until array.length()).mapNotNull { index -> array.optString(index).takeIf(String::isNotBlank) }
                }.orEmpty(),
            )
            "done" -> CloudAgentEvent.Completed(json.optLong("total_ms"))
            "error" -> CloudAgentEvent.Failed(
                message = json.optString("message", "Smara hit a problem."),
                recoverable = json.optBoolean("recoverable", true),
            )
            else -> null
        }
    }

    private fun open(path: String, method: String, session: String): HttpURLConnection =
        (URL(backendUrl.trimEnd('/') + path).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 15_000
            readTimeout = 30_000
            setRequestProperty("Cookie", "mem_session=$session")
            setRequestProperty("User-Agent", "Smara-Android/0.1")
        }

    private fun JSONObject.nullable(key: String): String? =
        if (isNull(key)) null else optString(key).takeIf { it.isNotBlank() }
}

class CloudSessionException(message: String) : IllegalStateException(message)
