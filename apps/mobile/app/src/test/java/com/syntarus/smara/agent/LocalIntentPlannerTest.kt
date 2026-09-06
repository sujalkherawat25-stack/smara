package com.syntarus.smara.agent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LocalIntentPlannerTest {
    private val planner = LocalIntentPlanner(CapabilityRegistry.default())

    @Test
    fun researchAndReminderBuildsDependencyGraph() {
        val steps = planner.plan("Research Android agents and remind me tomorrow")

        assertEquals(listOf("research", "reminder", "answer"), steps.map { it.id })
        assertEquals(setOf("research", "reminder"), steps.last().dependsOn)
        assertEquals("reason.deep", steps.last().capabilityId)
    }

    @Test
    fun plainDraftStaysOnDevice() {
        val steps = planner.plan("Draft a short thank-you note")

        assertEquals(1, steps.size)
        assertEquals("local.compose", steps.single().capabilityId)
        assertTrue(steps.single().dependsOn.isEmpty())
    }
}

