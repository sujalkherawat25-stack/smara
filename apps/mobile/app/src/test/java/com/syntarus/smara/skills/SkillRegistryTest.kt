package com.syntarus.smara.skills

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SkillRegistryTest {
    @Test
    fun `learned skill accepts only known capabilities`() {
        val registry = SkillRegistry.default()
        assertTrue(
            registry.registerLearned(
                SkillManifest(
                    id = "daily_research",
                    name = "Daily research",
                    version = 1,
                    description = "A learned research flow",
                    capabilityIds = listOf("memory.recall", "web.research"),
                    risk = SkillRisk.SAFE,
                    source = SkillSource.SYNTARUS_LEARNED,
                ),
            ),
        )
        assertFalse(
            registry.registerLearned(
                SkillManifest(
                    id = "unsafe",
                    name = "Unsafe",
                    version = 1,
                    description = "Attempts arbitrary execution",
                    capabilityIds = listOf("shell.exec"),
                    risk = SkillRisk.SAFE,
                    source = SkillSource.SYNTARUS_LEARNED,
                ),
            ),
        )
    }
}

