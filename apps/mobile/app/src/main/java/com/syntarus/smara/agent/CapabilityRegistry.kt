package com.syntarus.smara.agent

class CapabilityRegistry private constructor(
    private val capabilities: Map<String, AgentCapability>,
) {
    fun all(): List<AgentCapability> = capabilities.values.toList()
    fun get(id: String): AgentCapability? = capabilities[id]

    companion object {
        fun default() = CapabilityRegistry(
            listOf(
                AgentCapability("memory.recall", "Memory", "Recall trusted Syntarus context", CapabilityState.NEEDS_PAIRING, false),
                AgentCapability("web.research", "Research", "Search and compare live sources", CapabilityState.NEEDS_PAIRING, false),
                AgentCapability("reason.deep", "Reasoning", "Synthesize a careful final answer", CapabilityState.NEEDS_PAIRING, false),
                AgentCapability("device.reminder", "Reminders", "Schedule a reminder on this phone", CapabilityState.NEEDS_PERMISSION, true),
                AgentCapability("device.calendar", "Calendar", "Read or create calendar events", CapabilityState.PLANNED, true),
                AgentCapability("device.contacts", "Contacts", "Find a contact when a task needs one", CapabilityState.PLANNED, true),
                AgentCapability("device.files", "Files", "Choose a file without granting broad storage access", CapabilityState.AVAILABLE, true),
                AgentCapability("device.share", "Share", "Send a draft through any installed app", CapabilityState.AVAILABLE, true),
                AgentCapability("device.browser", "Browser", "Open a trusted link in your browser", CapabilityState.AVAILABLE, true),
                AgentCapability("local.compose", "Compose", "Draft and organize a response on-device", CapabilityState.AVAILABLE, true),
            ).associateBy { it.id },
        )
    }
}
