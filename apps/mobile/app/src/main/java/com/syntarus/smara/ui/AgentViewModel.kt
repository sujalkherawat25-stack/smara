package com.syntarus.smara.ui

import android.app.Activity
import android.content.Intent
import android.net.Uri
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.syntarus.smara.agent.AgentOrchestrator
import com.syntarus.smara.agent.AgentStep
import com.syntarus.smara.agent.AgentTask
import com.syntarus.smara.agent.CapabilityRegistry
import com.syntarus.smara.agent.StepState
import com.syntarus.smara.agent.TaskState
import com.syntarus.smara.auth.AccountSession
import com.syntarus.smara.auth.AuthRepository
import com.syntarus.smara.cloud.CloudAgentEvent
import com.syntarus.smara.cloud.CloudAgentRequest
import com.syntarus.smara.cloud.SmaraCloudGateway
import com.syntarus.smara.skills.SkillManifest
import com.syntarus.smara.skills.SkillRegistry
import com.syntarus.smara.skills.SkillRisk
import com.syntarus.smara.skills.SkillSource
import com.syntarus.smara.local.TaskNotifier
import com.syntarus.smara.local.FolderFileRepository
import com.syntarus.smara.local.DocumentOcrEngine
import com.syntarus.smara.local.OcrDocument
import android.graphics.Bitmap
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

data class AgentUiState(
    val prompt: String = "",
    val activeTask: AgentTask? = null,
    val history: List<AgentTask> = emptyList(),
    val running: Boolean = false,
    val account: AccountSession? = null,
    val checkingSession: Boolean = true,
    val authBusy: Boolean = false,
    val error: String? = null,
    val pendingApproval: PendingApproval? = null,
    val lastAnswer: String? = null,
)

data class PendingApproval(val id: String, val preview: String)

class AgentViewModel(
    private val orchestrator: AgentOrchestrator,
    val capabilities: CapabilityRegistry,
    private val auth: AuthRepository,
    private val cloud: SmaraCloudGateway,
    val skills: SkillRegistry,
    private val notifier: TaskNotifier,
    private val files: FolderFileRepository,
    private val ocr: DocumentOcrEngine,
) : ViewModel() {
    private val mutableState = MutableStateFlow(AgentUiState())
    val state: StateFlow<AgentUiState> = mutableState.asStateFlow()
    private var runJob: Job? = null

    init {
        viewModelScope.launch {
            runCatching { auth.restore() }
                .onSuccess { account -> mutableState.value = state.value.copy(account = account, checkingSession = false) }
                .onFailure { error -> mutableState.value = state.value.copy(checkingSession = false, error = error.safeMessage()) }
        }
    }

    fun updatePrompt(value: String) {
        mutableState.value = mutableState.value.copy(prompt = value)
    }

    /**
     * Submit a request.  Document requests are handed back to the Compose
     * Activity Result launcher instead of using the deprecated
     * startActivityForResult API.  The old path opened a picker but discarded
     * its result, which made "open/read my PDF" appear to do nothing.
     */
    fun submit(activity: Activity? = null, chooseDocument: ((String) -> Unit)? = null) {
        val request = state.value.prompt.trim()
        if (request.isBlank() || state.value.running) return
        runJob = viewModelScope.launch {
            mutableState.value = state.value.copy(prompt = "", running = true, lastAnswer = null, error = null)
            notifier.show(request.take(80))
            try {
                if (isDocumentRequest(request) && activity != null) runDocumentOpen(request, chooseDocument)
                else if (state.value.account == null) runLocal(request, activity) else runCloud(request)
            } catch (error: Throwable) {
                val current = state.value.activeTask ?: AgentTask(request = request)
                mutableState.value = state.value.copy(
                    activeTask = current.copy(state = TaskState.FAILED, answer = error.safeMessage()),
                    error = error.safeMessage(),
                )
            }
            val finished = state.value.activeTask
            mutableState.value = state.value.copy(
                running = false,
                activeTask = null,
                lastAnswer = finished?.answer ?: state.value.lastAnswer,
                history = if (finished == null) state.value.history else listOf(finished) + state.value.history,
            )
            notifier.complete(finished?.answer?.take(80) ?: "Your task is ready")
        }
    }

    fun scanDocument(uri: Uri) {
        runOcr("Read this document") { ocr.read(uri) }
    }

    fun documentSelectionCancelled() {
        mutableState.value = state.value.copy(
            error = "No file was selected. Tap Read a file and choose a PDF, image, or document.",
        )
    }

    fun scanCamera(bitmap: Bitmap) {
        runOcr("Scan this page") { ocr.readCamera(bitmap) }
    }

    private fun runOcr(request: String, reader: suspend () -> OcrDocument) {
        if (state.value.running) return
        runJob = viewModelScope.launch {
            mutableState.value = state.value.copy(running = true, lastAnswer = null, error = null)
            notifier.show(request)
            var task = AgentTask(request = request, state = TaskState.RUNNING)
                .upsert("ocr_local", "Read on this phone", "Recognising text privately", StepState.RUNNING)
            mutableState.value = state.value.copy(activeTask = task)
            try {
                val document = reader()
                task = task.upsert(
                    "ocr_local", "Read on this phone",
                    if (document.isUseful) "Text recognised" else "Cloud quality check needed",
                    if (document.isUseful) StepState.COMPLETED else StepState.FAILED,
                )
                mutableState.value = state.value.copy(activeTask = task)
                if (document.isUseful) {
                    val suffix = if (document.truncated) {
                        "\n\nLocal OCR read the first ${document.pagesRead} pages. Sign in and use cloud OCR for the complete document."
                    } else ""
                    task = task.copy(
                        state = TaskState.COMPLETED,
                        answer = "Text from ${document.fileName}:\n\n${document.text.take(16_000)}$suffix",
                    )
                    mutableState.value = state.value.copy(activeTask = task)
                } else if (state.value.account != null) {
                    task = task.upsert("ocr_cloud", "Improve with Smara OCR", "Uploading securely", StepState.RUNNING)
                    mutableState.value = state.value.copy(activeTask = task)
                    val attachment = cloud.upload(document.fileName, document.mimeType, document.bytes)
                    cloud.stream(
                        CloudAgentRequest(
                            message = "Read and faithfully extract the text from ${document.fileName}. Preserve headings, lists, tables, dates, and important fields. Tell me if anything is unreadable.",
                            conversationId = "android_${state.value.account!!.accountId.removePrefix("acct_")}",
                            attachmentIds = listOf(attachment.id),
                        ),
                    ).collect { event ->
                        task = project(task, event)
                        mutableState.value = state.value.copy(activeTask = task)
                    }
                } else {
                    task = task.copy(
                        state = TaskState.NEEDS_ATTENTION,
                        answer = "I could not read enough text locally. Sign in to use Smara cloud OCR for this complex or scanned document.",
                    )
                    mutableState.value = state.value.copy(activeTask = task)
                }
            } catch (error: Throwable) {
                task = task.copy(state = TaskState.FAILED, answer = error.safeMessage())
                mutableState.value = state.value.copy(activeTask = task, error = error.safeMessage())
            }
            val answer = task.answer ?: "Document scan finished."
            mutableState.value = state.value.copy(
                running = false,
                activeTask = null,
                lastAnswer = answer,
                history = listOf(task) + state.value.history,
            )
            notifier.complete(answer.take(80))
        }
    }

    private fun isDocumentRequest(request: String): Boolean {
        val text = request.lowercase()
        return (text.contains("resume") || text.contains("pdf") || text.contains("file")) &&
            (text.contains("open") || text.contains("find") || text.contains("show") || text.contains("choose"))
    }

    private suspend fun runDocumentOpen(request: String, chooseDocument: ((String) -> Unit)?) {
        val hint = request.lowercase()
            .replace(Regex("\\b(open|find|show|my|the|file|pdf|document|please|resume)\\b"), " ")
            .trim()
        val matches = if (files.hasFolder()) files.search(hint.ifBlank { request }, 5) else emptyList()
        if (matches.size == 1) {
            files.open(matches.first())
            mutableState.value = state.value.copy(activeTask = AgentTask(request = request, state = TaskState.COMPLETED, answer = "Opened ${matches.first().name} from your approved folder."))
            return
        }
        if (matches.size > 1) {
            mutableState.value = state.value.copy(activeTask = AgentTask(request = request, state = TaskState.COMPLETED, answer = "I found multiple matches:\n" + matches.joinToString("\n") { "• ${it.name}" } + "\nPlease be more specific."))
            return
        }
        // The caller owns the Activity Result launcher.  It remains alive
        // across configuration changes and routes the selected Uri to
        // scanDocument(), so OCR/cloud fallback actually runs.
        chooseDocument?.invoke(request)
        mutableState.value = state.value.copy(
            activeTask = AgentTask(
                request = request,
                state = TaskState.NEEDS_ATTENTION,
                answer = if (chooseDocument == null) {
                    "I couldn't find that file in the approved folder. Open Capabilities → Read a file to choose it."
                } else {
                    "I couldn't find a unique filename match. Choose the file and I will read it."
                },
            ),
        )
    }

    fun signIn(activity: Activity) {
        if (state.value.authBusy) return
        viewModelScope.launch {
            mutableState.value = state.value.copy(authBusy = true, error = null)
            runCatching { auth.signIn(activity) }
                .onSuccess { account -> mutableState.value = state.value.copy(account = account, authBusy = false) }
                .onFailure { error -> mutableState.value = state.value.copy(authBusy = false, error = error.safeMessage()) }
        }
    }

    fun switchAccount(activity: Activity) {
        if (state.value.authBusy) return
        viewModelScope.launch {
            mutableState.value = state.value.copy(authBusy = true, error = null)
            runCatching {
                auth.logout()
                auth.signIn(activity)
            }
                .onSuccess { account ->
                    mutableState.value = AgentUiState(account = account, checkingSession = false)
                }
                .onFailure { error ->
                    mutableState.value = state.value.copy(authBusy = false, error = error.safeMessage())
                }
        }
    }

    fun logout() {
        viewModelScope.launch {
            auth.logout()
            mutableState.value = AgentUiState(checkingSession = false)
        }
    }

    fun resolveApproval(approved: Boolean) {
        val approval = state.value.pendingApproval ?: return
        viewModelScope.launch {
            runCatching { cloud.resolveApproval(approval.id, approved) }
                .onSuccess { mutableState.value = state.value.copy(pendingApproval = null) }
                .onFailure { error -> mutableState.value = state.value.copy(error = error.safeMessage()) }
        }
    }

    fun cancel() {
        runJob?.cancel()
        mutableState.value = state.value.copy(running = false)
    }

    private suspend fun runLocal(request: String, activity: Activity?) {
        orchestrator.run(request).collect { task ->
            mutableState.value = state.value.copy(activeTask = task)
        }
        // Local side effects are deliberately narrow: Android shows the share sheet
        // or browser, and the user remains in control of the final action.
        val text = request.lowercase()
        if (activity != null && (text.contains("share") || text.contains("send this"))) {
            activity.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).apply {
                type = "text/plain"
                putExtra(Intent.EXTRA_TEXT, request)
            }, "Share with…"))
        } else if (activity != null && (text.contains("open syntarus") || text.contains("open website") || text.contains("open link"))) {
            activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://ai.syntarus.com")))
        }
    }

    private suspend fun runCloud(request: String) {
        var task = AgentTask(request = request, state = TaskState.RUNNING)
        mutableState.value = state.value.copy(activeTask = task, error = null)
        cloud.stream(
            CloudAgentRequest(
                message = request,
                conversationId = "android_${state.value.account!!.accountId.removePrefix("acct_")}",
            ),
        ).collect { event ->
            task = project(task, event)
            mutableState.value = state.value.copy(
                activeTask = task,
                pendingApproval = if (event is CloudAgentEvent.ApprovalRequired) {
                    PendingApproval(event.id, event.preview)
                } else state.value.pendingApproval,
            )
        }
    }

    private fun project(task: AgentTask, event: CloudAgentEvent): AgentTask = when (event) {
        is CloudAgentEvent.Phase -> task.upsert(
            id = "phase_${event.name}",
            title = event.name.replace('_', ' ').replaceFirstChar { it.titlecase() },
            detail = "In progress",
            state = StepState.RUNNING,
        )
        is CloudAgentEvent.Status -> task.upsert(
            id = "status",
            title = event.label,
            detail = event.detail ?: "In progress",
            state = StepState.RUNNING,
        )
        is CloudAgentEvent.MemorySearch -> task.upsert("memory", "Recall Syntarus memory", "${event.hits} relevant memories", StepState.COMPLETED)
        is CloudAgentEvent.CapabilityStarted -> task.upsert("tool_${event.name}", event.name.humanize(), "Running", StepState.RUNNING)
        is CloudAgentEvent.CapabilityFinished -> task.upsert(
            "tool_${event.name}",
            event.name.humanize(),
            if (event.ok) "Completed" else "Could not complete",
            if (event.ok) StepState.COMPLETED else StepState.FAILED,
        )
        is CloudAgentEvent.Token -> task.copy(answer = task.answer.orEmpty() + event.text)
        CloudAgentEvent.StreamReset -> task.copy(answer = "")
        is CloudAgentEvent.ApprovalRequired -> task.copy(state = TaskState.NEEDS_ATTENTION)
        is CloudAgentEvent.SkillLearned -> {
            skills.registerLearned(
                SkillManifest(
                    id = event.id,
                    name = event.name,
                    version = 1,
                    description = event.description,
                    capabilityIds = event.tools.mapNotNull(::mapToolCapability).distinct(),
                    risk = if (event.tools.any(::isSideEffectTool)) SkillRisk.CONFIRM else SkillRisk.SAFE,
                    source = SkillSource.SYNTARUS_LEARNED,
                ),
            )
            task.upsert("learned_${event.id}", "Learned ${event.name}", event.description, StepState.COMPLETED)
        }
        is CloudAgentEvent.Completed -> task.copy(
            state = TaskState.COMPLETED,
            steps = task.steps.map { if (it.state == StepState.RUNNING) it.copy(state = StepState.COMPLETED, detail = "Completed") else it },
        )
        is CloudAgentEvent.Failed -> task.copy(state = TaskState.FAILED, answer = event.message)
    }

    private fun AgentTask.upsert(id: String, title: String, detail: String, state: StepState): AgentTask {
        val existing = steps.indexOfFirst { it.id == id }
        val updated = if (existing < 0) {
            steps + AgentStep(id = id, title = title, capabilityId = id, state = state, detail = detail)
        } else {
            steps.mapIndexed { index, step -> if (index == existing) step.copy(state = state, detail = detail) else step }
        }
        return copy(state = TaskState.RUNNING, steps = updated)
    }

    private fun String.humanize() = replace('_', ' ').replaceFirstChar { it.titlecase() }

    private fun mapToolCapability(tool: String): String? = when {
        tool.contains("search") || tool == "fetch_url" -> "web.research"
        tool.contains("memory") || tool == "recall_actions" -> "memory.recall"
        tool.contains("reminder") -> "device.reminder"
        tool.contains("calendar") -> "device.calendar"
        tool.contains("document") || tool.contains("pdf") -> "device.files"
        tool.contains("email") -> "local.compose"
        else -> null
    }

    private fun isSideEffectTool(tool: String): Boolean =
        tool.startsWith("send_") || tool.startsWith("create_") || tool.startsWith("set_") || tool.startsWith("delete_")

    private fun Throwable.safeMessage() = message?.takeIf { it.isNotBlank() }
        ?: "Smara could not complete that request."
}
