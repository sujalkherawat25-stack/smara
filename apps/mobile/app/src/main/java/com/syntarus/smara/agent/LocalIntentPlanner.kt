package com.syntarus.smara.agent

class LocalIntentPlanner(private val registry: CapabilityRegistry) {
    fun plan(request: String): List<AgentStep> {
        val text = request.lowercase()
        val steps = mutableListOf<AgentStep>()

        if (text.containsAny("remember", "last time", "previous", "my preference")) {
            steps += step("memory", "Recall relevant memory", "memory.recall")
        }
        if (text.containsAny("search", "research", "latest", "compare", "find online", "news")) {
            steps += step("research", "Research trusted sources", "web.research")
        }
        if (text.containsAny("remind", "reminder", "alarm")) {
            steps += step("reminder", "Prepare phone reminder", "device.reminder")
        }
        if (text.containsAny("open file", "open pdf", "resume", "document")) {
            steps += step("files", "Open a file from this phone", "device.files")
        }
        if (text.containsAny("share", "send this")) {
            steps += step("share", "Prepare the share sheet", "device.share")
        }
        if (text.containsAny("open link", "open syntarus", "open website", "browse")) {
            steps += step("browser", "Open a trusted web page", "device.browser")
        }

        val evidenceSteps = steps.map { it.id }.toSet()
        val needsCloudReasoning = steps.any { !registry.get(it.capabilityId)!!.local }
        steps += if (needsCloudReasoning) {
            step("answer", "Synthesize the result", "reason.deep", evidenceSteps)
        } else {
            step("answer", "Compose the result", "local.compose", evidenceSteps)
        }
        return steps
    }

    private fun step(id: String, title: String, capability: String, deps: Set<String> = emptySet()) =
        AgentStep(id = id, title = title, capabilityId = capability, dependsOn = deps)

    private fun String.containsAny(vararg values: String) = values.any(::contains)
}
