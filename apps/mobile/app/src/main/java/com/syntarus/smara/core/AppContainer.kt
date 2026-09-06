package com.syntarus.smara.core

import android.content.Context
import com.syntarus.smara.agent.AgentOrchestrator
import com.syntarus.smara.agent.CapabilityRegistry
import com.syntarus.smara.agent.LocalIntentPlanner
import com.syntarus.smara.auth.AuthApi
import com.syntarus.smara.auth.AuthRepository
import com.syntarus.smara.cloud.HttpSmaraCloudGateway
import com.syntarus.smara.data.DeviceSettingsRepository
import com.syntarus.smara.security.SecureTokenStore
import com.syntarus.smara.skills.SkillRegistry
import com.syntarus.smara.local.TaskNotifier
import com.syntarus.smara.local.FolderFileRepository
import com.syntarus.smara.local.DocumentOcrEngine

class AppContainer(context: Context) {
    private val backendUrl = "https://ai.syntarus.com"
    val settings = DeviceSettingsRepository(context)
    val tokenStore = SecureTokenStore(context)
    val auth = AuthRepository(AuthApi(backendUrl, tokenStore))
    val cloud = HttpSmaraCloudGateway(backendUrl, tokenStore)
    val taskNotifier = TaskNotifier(context)
    val files = FolderFileRepository(context)
    val ocr = DocumentOcrEngine(context)
    val capabilities = CapabilityRegistry.default()
    val skills = SkillRegistry.default()
    val orchestrator = AgentOrchestrator(
        planner = LocalIntentPlanner(capabilities),
        capabilities = capabilities,
    )
}
