package com.syntarus.smara.ui

import androidx.activity.compose.LocalActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import android.content.Intent
import android.net.Uri
import android.speech.RecognizerIntent
import android.provider.CalendarContract
import android.app.AlarmManager
import com.syntarus.smara.local.PhoneTools
import com.syntarus.smara.local.FolderFileRepository
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.syntarus.smara.agent.*
import com.syntarus.smara.core.AppContainer

@Composable
fun SmaraApp(container: AppContainer) {
    val activity = requireNotNull(LocalActivity.current) { "Smara requires an Activity host" }
    val vm: AgentViewModel = viewModel {
        AgentViewModel(container.orchestrator, container.capabilities, container.auth, container.cloud, container.skills, container.taskNotifier, container.files, container.ocr)
    }
    val state by vm.state.collectAsStateWithLifecycle()
    var selectedTab by rememberSaveable { mutableIntStateOf(0) }
    val documentPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
        if (uri == null) vm.documentSelectionCancelled() else vm.scanDocument(uri)
    }

    Scaffold(
        containerColor = Color(0xFF080B0D),
        topBar = { SmaraHeader() },
        bottomBar = {
            Column {
                if (selectedTab == 0) PromptBar(
                    value = state.prompt,
                    running = state.running,
                    onValueChange = vm::updatePrompt,
                    onSubmit = {
                        vm.submit(activity) { request ->
                            documentPicker.launch(
                                if (request.lowercase().contains("pdf")) arrayOf("application/pdf")
                                else arrayOf("*/*"),
                            )
                        }
                    },
                    onCancel = vm::cancel,
                    onVoiceText = vm::updatePrompt,
                )
                NavigationBar(containerColor = Color(0xFF0C1113)) {
                    listOf("Agent" to "⌂", "Capabilities" to "✦", "Memory" to "◈", "Account" to "●").forEachIndexed { index, item ->
                        NavigationBarItem(
                            selected = selectedTab == index,
                            onClick = { selectedTab = index },
                            icon = { Text(item.second, fontSize = 18.sp) },
                            label = { Text(item.first, fontSize = 10.sp) },
                            colors = NavigationBarItemDefaults.colors(
                                selectedIconColor = Color(0xFF07100A),
                                selectedTextColor = Color(0xFFB7F20A),
                                indicatorColor = Color(0xFFB7F20A),
                                unselectedIconColor = Color(0xFF839398),
                                unselectedTextColor = Color(0xFF839398),
                            ),
                        )
                    }
                }
            }
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp, vertical = 16.dp),
            verticalArrangement = Arrangement.spacedBy(18.dp),
        ) {
            when (selectedTab) {
                0 -> {
                    AgentPresence(running = state.running)
                    state.error?.let { ErrorCard(it) }
                    state.activeTask?.let { ExecutionCard(it) }
                    state.lastAnswer?.let { AnswerCard(it) }
                    if (state.activeTask == null && state.lastAnswer == null && !state.running) WelcomeCard()
                    state.pendingApproval?.let { approval ->
                        ApprovalCard(approval, onApprove = { vm.resolveApproval(true) }, onDeny = { vm.resolveApproval(false) })
                    }
                }
                1 -> {
                    PageIntro("Capabilities", "See what Smara can do on this phone and what becomes available after account or permission approval.")
                    CapabilityStrip(vm.capabilities.all(), connected = state.account != null)
                    LocalActions(
                        files = container.files,
                        onDocument = { uri -> vm.scanDocument(uri); selectedTab = 0 },
                        onCamera = { bitmap -> vm.scanCamera(bitmap); selectedTab = 0 },
                    )
                }
                2 -> {
                    PageIntro("Memory and skills", "Your durable Syntarus memory stays connected to the same account used on the web.")
                    if (state.history.isNotEmpty()) {
                        Text("Recent work", color = Color.White, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
                        state.history.take(8).forEach { HistoryRow(it) }
                    } else EmptyState("No phone activity yet", "Ask Smara to research, remember, or plan something.")
                    SkillStrip(vm.skills.all())
                }
                else -> {
                    PageIntro("Account", "Control which Syntarus account this phone uses. Switching opens the browser so the account and its memories stay consistent everywhere.")
                    AccountCard(state, onSignIn = { vm.signIn(activity) }, onSwitch = { vm.switchAccount(activity) }, onLogout = vm::logout)
                    state.account?.let { account ->
                        Surface(color = Color(0xFF101518), shape = RoundedCornerShape(20.dp), modifier = Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
                                Text("Connected account", color = Color.White, fontWeight = FontWeight.SemiBold)
                                AccountDetail("Account ID", account.accountId)
                                AccountDetail("Plan", account.plan.replaceFirstChar { it.titlecase() })
                                account.email?.let { AccountDetail("Email", it) }
                                Text("Memory is shared with Smara web and Telegram for this account.", color = Color(0xFF839398), fontSize = 12.sp)
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun PageIntro(title: String, description: String) {
    Text(title, color = Color.White, fontSize = 26.sp, fontWeight = FontWeight.Bold)
    Text(description, color = Color(0xFF9AA8AE), fontSize = 14.sp, lineHeight = 20.sp)
}

@Composable
private fun LocalActions(
    files: FolderFileRepository,
    onDocument: (Uri) -> Unit,
    onCamera: (android.graphics.Bitmap) -> Unit,
) {
    val activity = LocalActivity.current
    var notice by remember { mutableStateOf<String?>(null) }
    var downloadUrl by remember { mutableStateOf("") }
    var folderNotice by remember { mutableStateOf(if (files.hasFolder()) "An approved folder is connected." else "Choose a folder once so Smara can search it by name.") }
    var showReminderApproval by remember { mutableStateOf(false) }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
        if (uri == null) notice = "File selection cancelled."
        else onDocument(uri)
    }
    val camera = rememberLauncherForActivityResult(ActivityResultContracts.TakePicturePreview()) { bitmap ->
        if (bitmap == null) notice = "Scan cancelled." else onCamera(bitmap)
    }
    val folderPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri: Uri? ->
        if (uri != null) { files.saveFolder(uri); folderNotice = "Folder approved. Smara can now search it by filename." }
        else folderNotice = "Folder selection cancelled. Tap Choose folder and select a folder to continue."
    }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text("Run locally", color = Color.White, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
        Text("These actions stay on your phone. Smara asks before anything that changes data or leaves the device.", color = Color(0xFF839398), fontSize = 12.sp, lineHeight = 18.sp)
        Surface(color = Color(0xFF142018), shape = RoundedCornerShape(16.dp), modifier = Modifier.fillMaxWidth()) {
            Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Column(Modifier.weight(1f)) {
                    Text("Filename search", color = Color.White, fontWeight = FontWeight.Medium)
                    Text(folderNotice, color = Color(0xFF8CECBF), fontSize = 11.sp)
                }
                TextButton(onClick = { folderPicker.launch(null) }) { Text("Choose folder", color = Color(0xFFB7F20A)) }
            }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(9.dp)) {
            LocalActionCard("Read a file", "Local OCR + quality fallback", Modifier.weight(1f)) {
                picker.launch(arrayOf("*/*"))
            }
            LocalActionCard("Scan a page", "Camera OCR on this phone", Modifier.weight(1f)) { camera.launch(null) }
        }
        LocalActionCard("Share text", "Use any app", Modifier.fillMaxWidth()) {
            activity?.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).apply {
                type = "text/plain"
                putExtra(Intent.EXTRA_TEXT, "Drafted with Smara")
            }, "Share with…"))
            notice = "Share sheet opened."
        }
        LocalActionCard("Open Syntarus", "View your connected memory", Modifier.fillMaxWidth()) {
            activity?.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://ai.syntarus.com")))
            notice = "Syntarus opened in your browser."
        }
        OutlinedTextField(
            value = downloadUrl,
            onValueChange = { downloadUrl = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Background download URL") },
            placeholder = { Text("https://…") },
            singleLine = true,
        )
        Button(
            onClick = {
                val url = downloadUrl.trim()
                if (url.startsWith("https://")) {
                    PhoneTools.enqueueDownload(requireNotNull(activity), url, "smara-download-${System.currentTimeMillis()}")
                    notice = "Download started. You’ll get a notification when it is ready."
                } else notice = "Use a secure https:// link."
            },
            enabled = downloadUrl.isNotBlank(),
            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFB7F20A)),
        ) { Text("Download in background", color = Color(0xFF07100A)) }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(9.dp)) {
            LocalActionCard("Set reminder", "Ask before scheduling", Modifier.weight(1f)) { showReminderApproval = true }
            LocalActionCard("Add calendar event", "Opens Android approval", Modifier.weight(1f)) {
                activity?.startActivity(Intent(Intent.ACTION_INSERT).setData(CalendarContract.Events.CONTENT_URI).putExtra(CalendarContract.Events.TITLE, "Smara task"))
                notice = "Calendar draft opened. Review and save it yourself."
            }
        }
        if (showReminderApproval) {
            AlertDialog(
                onDismissRequest = { showReminderApproval = false },
                title = { Text("Schedule a reminder?") },
                text = { Text("This will create a local reminder for 10 minutes from now. You can cancel it from Android notifications.") },
                confirmButton = {
                    TextButton(onClick = {
                        PhoneTools.scheduleReminder(requireNotNull(activity), "Check in with Smara", System.currentTimeMillis() + 10 * 60 * 1000)
                        notice = "Reminder scheduled for 10 minutes from now."
                        showReminderApproval = false
                    }) { Text("Schedule") }
                },
                dismissButton = { TextButton(onClick = { showReminderApproval = false }) { Text("Cancel") } },
            )
        }
        notice?.let { Text(it, color = Color(0xFF8CECBF), fontSize = 12.sp) }
    }
}

@Composable
private fun LocalActionCard(title: String, detail: String, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Surface(color = Color(0xFF101518), shape = RoundedCornerShape(16.dp), modifier = modifier) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(title, color = Color.White, fontWeight = FontWeight.Medium)
            Text(detail, color = Color(0xFF839398), fontSize = 11.sp, minLines = 2)
            TextButton(onClick = onClick, contentPadding = PaddingValues(0.dp)) {
                Text("Use →", color = Color(0xFFB7F20A), fontSize = 12.sp)
            }
        }
    }
}

@Composable
private fun EmptyState(title: String, detail: String) {
    Surface(color = Color(0xFF101518), shape = RoundedCornerShape(20.dp), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(title, color = Color.White, fontWeight = FontWeight.SemiBold)
            Text(detail, color = Color(0xFF839398), fontSize = 13.sp)
        }
    }
}

@Composable
private fun AccountDetail(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, color = Color(0xFF839398), fontSize = 13.sp)
        Text(value, color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.Medium)
    }
}

@Composable
private fun SkillStrip(items: List<com.syntarus.smara.skills.SkillManifest>) {
    Column(verticalArrangement = Arrangement.spacedBy(9.dp)) {
        Text("Skills", color = Color.White, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
        Text(
            "Reusable workflows. Learned skills can combine approved capabilities, never install executable code.",
            color = Color(0xFF839398),
            fontSize = 12.sp,
        )
        items.take(6).forEach { skill ->
            Surface(color = Color(0xFF101518), shape = RoundedCornerShape(16.dp), modifier = Modifier.fillMaxWidth()) {
                Row(
                    Modifier.padding(13.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(skill.name, color = Color.White, fontWeight = FontWeight.Medium)
                        Text(skill.description, color = Color(0xFF839398), fontSize = 11.sp)
                    }
                    Text(
                        if (skill.source == com.syntarus.smara.skills.SkillSource.BUILT_IN) "Built in" else "Learned",
                        color = Color(0xFF8CECBF),
                        fontSize = 11.sp,
                    )
                }
            }
        }
    }
}

@Composable
private fun SmaraHeader() {
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            SmaraOrb(30.dp)
            Text("Smara", color = Color.White, fontSize = 20.sp, fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
private fun AccountCard(
    state: AgentUiState,
    onSignIn: () -> Unit,
    onSwitch: () -> Unit,
    onLogout: () -> Unit,
) {
    if (state.checkingSession) {
        LinearProgressIndicator(
            modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(99.dp)),
            color = Color(0xFFB7F20A),
            trackColor = Color(0xFF182024),
        )
        return
    }
    val account = state.account
    Surface(
        color = if (account == null) Color(0xFF142018) else Color(0xFF101A16),
        shape = RoundedCornerShape(20.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            Modifier.padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    if (account == null) "Connect your Smara account" else "Syntarus account connected",
                    color = Color.White,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    if (account == null) "We will open Smara in your browser. Approve this phone to share one memory."
                    else listOfNotNull(account.displayName, account.email).joinToString(" · "),
                    color = Color(0xFF91A39B),
                    fontSize = 12.sp,
                )
            }
            if (account == null) {
                Button(
                    onClick = onSignIn,
                    enabled = !state.authBusy,
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFB7F20A)),
                ) {
                    Text(if (state.authBusy) "Waiting..." else "Connect Smara", color = Color(0xFF07100A))
                }
            } else {
                Row(horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                    TextButton(onClick = onSwitch, enabled = !state.authBusy) { Text("Switch", color = Color(0xFFB7F20A)) }
                    TextButton(onClick = onLogout, enabled = !state.authBusy) { Text("Sign out", color = Color(0xFF9AABA2)) }
                }
            }
        }
    }
}

@Composable
private fun ErrorCard(message: String) {
    Surface(color = Color(0xFF2B1717), shape = RoundedCornerShape(16.dp), modifier = Modifier.fillMaxWidth()) {
        Text(message, color = Color(0xFFFFB4AB), modifier = Modifier.padding(14.dp), fontSize = 13.sp)
    }
}

@Composable
private fun ApprovalCard(
    approval: PendingApproval,
    onApprove: () -> Unit,
    onDeny: () -> Unit,
) {
    Surface(color = Color(0xFF251F12), shape = RoundedCornerShape(20.dp), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Your approval is required", color = Color.White, fontWeight = FontWeight.SemiBold)
            Text(approval.preview, color = Color(0xFFE3D6B8), fontSize = 14.sp)
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Button(onClick = onApprove, colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFB7F20A))) {
                    Text("Approve", color = Color(0xFF07100A))
                }
                OutlinedButton(onClick = onDeny) { Text("Deny") }
            }
        }
    }
}

@Composable
private fun AgentPresence(running: Boolean) {
    Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxWidth()) {
        SmaraOrb(if (running) 96.dp else 88.dp)
        Spacer(Modifier.height(14.dp))
        Text(if (running) "Smara is coordinating" else greeting(), color = Color.White, fontSize = 25.sp, fontWeight = FontWeight.Bold)
        if (running) Text("You can see every capability involved.", color = Color(0xFF9AA8AE), fontSize = 14.sp)
    }
}

private fun greeting(): String {
    val hour = java.util.Calendar.getInstance().get(java.util.Calendar.HOUR_OF_DAY)
    return when (hour) {
        in 5..11 -> "Good morning"
        in 12..16 -> "Good afternoon"
        in 17..21 -> "Good evening"
        else -> "Good night"
    }
}

@Composable
private fun SmaraOrb(size: androidx.compose.ui.unit.Dp) {
    Box(
        Modifier.size(size).clip(CircleShape).background(
            Brush.verticalGradient(
                0f to Color(0xFF164D2D),
                0.28f to Color(0xFF68BC20),
                1f to Color(0xFFB7F20A),
            ),
        ),
    )
}

@Composable
private fun WelcomeCard() {
    Surface(color = Color(0xFF101518), shape = RoundedCornerShape(24.dp), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("Try the agent foundation", color = Color.White, fontWeight = FontWeight.SemiBold)
            Text("“Research the latest Android agent frameworks and remind me tomorrow.”", color = Color(0xFFD5DEE1))
            Text("Smara will build a visible task graph and show which permissions or account connections are needed.", color = Color(0xFF8B9AA0), fontSize = 13.sp)
        }
    }
}

@Composable
private fun AnswerCard(answer: String) {
    Surface(color = Color(0xFF101A16), shape = RoundedCornerShape(24.dp), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Smara", color = Color(0xFFB7F20A), fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
            Text(answer, color = Color(0xFFF2F5F4), fontSize = 15.sp, lineHeight = 22.sp)
        }
    }
}

@Composable
private fun ExecutionCard(task: AgentTask) {
    Surface(color = Color(0xFF101518), shape = RoundedCornerShape(24.dp), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                Text("Smara is working", color = Color.White, fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                Text("${task.steps.count { it.state == StepState.COMPLETED }}/${task.steps.size}", color = Color(0xFFB7F20A), fontSize = 12.sp)
            }
            Text(task.request, color = Color(0xFFB8C6C9), fontSize = 13.sp, maxLines = 3)
            task.steps.forEachIndexed { index, step ->
                Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    StepDot(index + 1, step.state)
                    Column {
                        Text(step.title, color = Color(0xFFF2F5F4), fontWeight = FontWeight.Medium)
                        Text(step.detail, color = step.state.detailColor(), fontSize = 12.sp)
                    }
                }
            }
            task.answer?.let {
                HorizontalDivider(color = Color(0xFF283236))
                Text(it, color = Color(0xFFCEE2D8), fontSize = 14.sp)
            }
        }
    }
}

@Composable
private fun StepDot(number: Int, state: StepState) {
    val color = when (state) {
        StepState.COMPLETED -> Color(0xFF78E6AC)
        StepState.RUNNING -> Color(0xFFB7F20A)
        StepState.BLOCKED -> Color(0xFFF0B65E)
        StepState.FAILED -> Color(0xFFFF7777)
        StepState.WAITING -> Color(0xFF38454A)
    }
    Box(Modifier.size(28.dp).clip(CircleShape).background(color), contentAlignment = Alignment.Center) {
        Text(number.toString(), color = Color(0xFF07100A), fontSize = 12.sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun CapabilityStrip(items: List<AgentCapability>, connected: Boolean) {
    Column(verticalArrangement = Arrangement.spacedBy(9.dp)) {
        Text("Capabilities", color = Color.White, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
        items.chunked(2).forEach { rowItems ->
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(9.dp)) {
                rowItems.forEach { capability ->
                    Surface(
                        color = Color(0xFF101518),
                        shape = RoundedCornerShape(16.dp),
                        modifier = Modifier.weight(1f),
                    ) {
                        Column(Modifier.padding(13.dp)) {
                            Text(capability.label, color = Color.White, fontWeight = FontWeight.Medium)
                            Text(capability.state.label(connected), color = Color(0xFF839398), fontSize = 11.sp)
                        }
                    }
                }
                if (rowItems.size == 1) Spacer(Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun HistoryRow(task: AgentTask) {
    Surface(color = Color(0xFF0D1214), shape = RoundedCornerShape(16.dp), modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(14.dp), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(task.request, color = Color(0xFFD7DFE2), modifier = Modifier.weight(1f), maxLines = 1)
            Text(task.state.name.lowercase().replaceFirstChar { it.titlecase() }, color = Color(0xFF7E908F), fontSize = 12.sp)
        }
    }
}

@Composable
private fun PromptBar(
    value: String,
    running: Boolean,
    onValueChange: (String) -> Unit,
    onSubmit: () -> Unit,
    onCancel: () -> Unit,
    onVoiceText: (String) -> Unit,
) {
    val voice = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val text = result.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()
        if (result.resultCode == android.app.Activity.RESULT_OK && !text.isNullOrBlank()) onVoiceText(text)
    }
    Surface(color = Color(0xFF0C1113), shadowElevation = 12.dp) {
        Row(
            Modifier.navigationBarsPadding().imePadding().padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            OutlinedTextField(
                value = value,
                onValueChange = onValueChange,
                modifier = Modifier.weight(1f),
                placeholder = { Text("Ask Smara to do something") },
                enabled = !running,
                shape = RoundedCornerShape(20.dp),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedTextColor = Color.White,
                    unfocusedTextColor = Color.White,
                    focusedBorderColor = Color(0xFF4C705C),
                    unfocusedBorderColor = Color(0xFF283236),
                ),
                maxLines = 3,
            )
            TextButton(onClick = {
                voice.launch(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    putExtra(RecognizerIntent.EXTRA_PROMPT, "Speak to Smara")
                })
            }, enabled = !running, contentPadding = PaddingValues(0.dp)) {
                Text("Mic", color = Color(0xFFB7F20A), fontSize = 12.sp)
            }
            Button(
                onClick = if (running) onCancel else onSubmit,
                colors = ButtonDefaults.buttonColors(containerColor = if (running) Color(0xFF374247) else Color(0xFFB7F20A)),
                shape = CircleShape,
                contentPadding = PaddingValues(0.dp),
                modifier = Modifier.size(52.dp),
            ) {
                Text(if (running) "■" else "↑", color = Color(0xFF07100A), fontWeight = FontWeight.Bold)
            }
        }
    }
}

private fun CapabilityState.label(connected: Boolean) = when (this) {
    CapabilityState.AVAILABLE -> "Ready on device"
    CapabilityState.NEEDS_PERMISSION -> "Permission needed"
    CapabilityState.NEEDS_PAIRING -> if (connected) "Connected to Smara" else "Sign in required"
    CapabilityState.PLANNED -> "Coming next"
}

private fun StepState.detailColor() = when (this) {
    StepState.COMPLETED -> Color(0xFF78E6AC)
    StepState.RUNNING -> Color(0xFFB7F20A)
    StepState.BLOCKED -> Color(0xFFF0B65E)
    StepState.FAILED -> Color(0xFFFF7777)
    StepState.WAITING -> Color(0xFF7F8D92)
}
