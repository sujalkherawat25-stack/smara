package com.syntarus.smara.agent

import java.util.UUID

enum class CapabilityState { AVAILABLE, NEEDS_PERMISSION, NEEDS_PAIRING, PLANNED }
enum class StepState { WAITING, RUNNING, COMPLETED, BLOCKED, FAILED }
enum class TaskState { PLANNING, RUNNING, COMPLETED, NEEDS_ATTENTION, FAILED }

data class AgentCapability(
    val id: String,
    val label: String,
    val description: String,
    val state: CapabilityState,
    val local: Boolean,
)

data class AgentStep(
    val id: String,
    val title: String,
    val capabilityId: String,
    val dependsOn: Set<String> = emptySet(),
    val state: StepState = StepState.WAITING,
    val detail: String = "Waiting",
)

data class AgentTask(
    val id: String = UUID.randomUUID().toString(),
    val request: String,
    val state: TaskState = TaskState.PLANNING,
    val steps: List<AgentStep> = emptyList(),
    val answer: String? = null,
)

