package com.syntarus.smara.auth

import com.syntarus.smara.security.SecureTokenStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

data class DeviceLinkStart(
    val requestId: String,
    val deviceSecret: String,
    val verificationUrl: String,
    val expiresInSeconds: Int,
)

class AuthApi(
    private val backendUrl: String,
    private val tokenStore: SecureTokenStore,
) {
    suspend fun startDeviceLink(): DeviceLinkStart = withContext(Dispatchers.IO) {
        open("/v1/auth/device/start", "POST").useResponse { code, body ->
            if (code !in 200..299) error(serverMessage(code, body))
            val json = JSONObject(body)
            DeviceLinkStart(
                requestId = json.getString("request_id"),
                deviceSecret = json.getString("device_secret"),
                verificationUrl = json.getString("verification_url"),
                expiresInSeconds = json.optInt("expires_in_seconds", 600),
            )
        }
    }

    suspend fun waitForDeviceApproval(link: DeviceLinkStart): AccountSession {
        val deadline = System.currentTimeMillis() + link.expiresInSeconds * 1_000L
        while (System.currentTimeMillis() < deadline) {
            val result = pollDeviceLink(link)
            if (result != null) return result
            delay(2_000)
        }
        error("The phone connection expired. Tap Connect Smara and try again.")
    }

    suspend fun restore(): AccountSession? = withContext(Dispatchers.IO) {
        val token = tokenStore.read() ?: return@withContext null
        open("/v1/auth/me", "GET", token).useResponse { code, body ->
            when (code) {
                in 200..299 -> parseAccount(JSONObject(body))
                401 -> {
                    tokenStore.clear()
                    null
                }
                else -> error(serverMessage(code, body))
            }
        }
    }

    suspend fun logout() = withContext(Dispatchers.IO) {
        val token = tokenStore.read()
        if (token != null) runCatching {
            open("/v1/auth/logout", "POST", token).useResponse { _, _ -> Unit }
        }
        tokenStore.clear()
    }

    private suspend fun pollDeviceLink(link: DeviceLinkStart): AccountSession? = withContext(Dispatchers.IO) {
        val payload = JSONObject()
            .put("request_id", link.requestId)
            .put("device_secret", link.deviceSecret)
        val connection = open("/v1/auth/device/token", "POST").apply {
            setRequestProperty("Content-Type", "application/json")
            doOutput = true
            outputStream.bufferedWriter().use { it.write(payload.toString()) }
        }
        connection.useResponse { code, body ->
            when (code) {
                202 -> null
                in 200..299 -> {
                    val json = JSONObject(body)
                    tokenStore.save(json.getString("session_token"))
                    parseAccount(json)
                }
                else -> error(serverMessage(code, body))
            }
        }
    }

    private fun parseAccount(json: JSONObject): AccountSession = AccountSession(
        accountId = json.getString("account_id"),
        email = json.nullableString("email"),
        displayName = json.nullableString("display_name"),
        avatarUrl = json.nullableString("avatar_url"),
        plan = json.optString("plan", "free"),
    )

    private fun open(path: String, method: String, token: String? = null): HttpURLConnection =
        (URL(backendUrl.trimEnd('/') + path).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 15_000
            readTimeout = 30_000
            setRequestProperty("Accept", "application/json")
            setRequestProperty("User-Agent", "Smara-Android/0.1")
            if (token != null) setRequestProperty("Cookie", "mem_session=$token")
        }

    private inline fun <T> HttpURLConnection.useResponse(block: (Int, String) -> T): T = try {
        val code = responseCode
        val stream = if (code in 200..299) inputStream else errorStream
        block(code, stream?.bufferedReader()?.use { it.readText() }.orEmpty())
    } finally {
        disconnect()
    }

    private fun serverMessage(code: Int, body: String): String {
        val detail = runCatching { JSONObject(body).optString("detail") }.getOrNull()
        return detail?.takeIf { it.isNotBlank() } ?: "Smara account connection failed ($code)."
    }

    private fun JSONObject.nullableString(key: String): String? =
        if (isNull(key)) null else optString(key).takeIf { it.isNotBlank() }
}
