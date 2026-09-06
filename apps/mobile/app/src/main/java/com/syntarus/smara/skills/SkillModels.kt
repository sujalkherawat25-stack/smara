package com.syntarus.smara.skills

enum class SkillRisk { SAFE, CONFIRM }
enum class SkillSource { BUILT_IN, SYNTARUS_LEARNED, SIGNED_CATALOG }

/**
 * A skill is data, never downloaded executable code. The orchestrator resolves
 * every capability through the local registry, which still enforces Android
 * permissions and the confirmation tier at execution time.
 */
data class SkillManifest(
    val id: String,
    val name: String,
    val version: Int,
    val description: String,
    val capabilityIds: List<String>,
    val risk: SkillRisk,
    val source: SkillSource,
    val enabled: Boolean = true,
)

