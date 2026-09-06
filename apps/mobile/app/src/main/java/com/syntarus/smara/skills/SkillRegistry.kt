package com.syntarus.smara.skills

class SkillRegistry private constructor(
    private val skills: LinkedHashMap<String, SkillManifest>,
) {
    fun all(): List<SkillManifest> = skills.values.toList()

    fun registerLearned(skill: SkillManifest): Boolean {
        if (skill.source != SkillSource.SYNTARUS_LEARNED) return false
        if (skill.id.isBlank() || skill.capabilityIds.isEmpty()) return false
        if (skill.capabilityIds.any { it !in ALLOWED_CAPABILITIES }) return false
        val previous = skills[skill.id]
        if (previous != null && previous.version > skill.version) return false
        skills[skill.id] = skill
        return true
    }

    companion object {
        private val ALLOWED_CAPABILITIES = setOf(
            "memory.recall",
            "web.research",
            "reason.deep",
            "device.reminder",
            "device.calendar",
            "device.contacts",
            "device.files",
            "local.compose",
        )

        fun default() = SkillRegistry(
            linkedMapOf(
                "research_brief" to SkillManifest(
                    id = "research_brief",
                    name = "Research brief",
                    version = 1,
                    description = "Recall context, research current sources, and produce a focused brief.",
                    capabilityIds = listOf("memory.recall", "web.research", "local.compose"),
                    risk = SkillRisk.SAFE,
                    source = SkillSource.BUILT_IN,
                ),
                "meeting_prep" to SkillManifest(
                    id = "meeting_prep",
                    name = "Meeting prep",
                    version = 1,
                    description = "Collect relationship context and prepare an agenda before a meeting.",
                    capabilityIds = listOf("memory.recall", "device.calendar", "local.compose"),
                    risk = SkillRisk.SAFE,
                    source = SkillSource.BUILT_IN,
                ),
                "follow_up" to SkillManifest(
                    id = "follow_up",
                    name = "Follow-up",
                    version = 1,
                    description = "Draft a follow-up and schedule a reminder after explicit approval.",
                    capabilityIds = listOf("memory.recall", "local.compose", "device.reminder"),
                    risk = SkillRisk.CONFIRM,
                    source = SkillSource.BUILT_IN,
                ),
            ),
        )
    }
}

