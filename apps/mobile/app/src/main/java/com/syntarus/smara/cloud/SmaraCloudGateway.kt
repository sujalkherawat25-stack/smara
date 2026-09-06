package com.syntarus.smara.cloud

import kotlinx.coroutines.flow.Flow

/**
 * Native boundary to the existing Smara SSE agent path.
 *
 * Google sign-in exchanges an ID token for the existing Smara account session.
 * The session is encrypted with Android Keystore and this boundary keeps the
 * UI and device tools independent of HTTP and any model provider.
 */
interface SmaraCloudGateway {
    fun stream(request: CloudAgentRequest): Flow<CloudAgentEvent>
    suspend fun upload(fileName: String, mimeType: String, bytes: ByteArray): CloudAttachment
    suspend fun resolveApproval(confirmId: String, approved: Boolean)
}

data class CloudAgentRequest(
    val message: String,
    val conversationId: String,
    val deepReasoning: Boolean = false,
    val attachmentIds: List<String> = emptyList(),
)

data class CloudAttachment(
    val id: String,
    val fileName: String,
    val kind: String,
    val characters: Int = 0,
)

sealed interface CloudAgentEvent {
    data class Phase(val name: String) : CloudAgentEvent
    data class Status(val label: String, val detail: String?) : CloudAgentEvent
    data class MemorySearch(val query: String, val hits: Int) : CloudAgentEvent
    data class CapabilityStarted(val name: String) : CloudAgentEvent
    data class CapabilityFinished(val name: String, val ok: Boolean) : CloudAgentEvent
    data class Token(val text: String) : CloudAgentEvent
    data object StreamReset : CloudAgentEvent
    data class ApprovalRequired(val id: String, val preview: String) : CloudAgentEvent
    data class SkillLearned(
        val id: String,
        val name: String,
        val description: String,
        val tools: List<String>,
    ) : CloudAgentEvent
    data class Completed(val totalMs: Long) : CloudAgentEvent
    data class Failed(val message: String, val recoverable: Boolean) : CloudAgentEvent
}
