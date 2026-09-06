package com.syntarus.smara.agent

import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

class AgentOrchestrator(
    private val planner: LocalIntentPlanner,
    private val capabilities: CapabilityRegistry,
) {
    fun run(request: String): Flow<AgentTask> = flow {
        var task = AgentTask(request = request.trim())
        emit(task)
        delay(120)

        task = task.copy(state = TaskState.RUNNING, steps = planner.plan(request))
        emit(task)

        for (index in task.steps.indices) {
            val step = task.steps[index]
            val capability = capabilities.get(step.capabilityId) ?: continue
            val dependenciesComplete = step.dependsOn.all { dependency ->
                task.steps.firstOrNull { it.id == dependency }?.state == StepState.COMPLETED
            }
            if (!dependenciesComplete) {
                task = task.update(index, StepState.BLOCKED, "Waiting for a required step")
                emit(task)
                continue
            }

            task = task.update(index, StepState.RUNNING, "Smara is working")
            emit(task)
            delay(280)

            val (state, detail) = when (capability.state) {
                CapabilityState.AVAILABLE -> StepState.COMPLETED to "Completed on this phone"
                CapabilityState.NEEDS_PERMISSION -> StepState.BLOCKED to "Permission required before execution"
                CapabilityState.NEEDS_PAIRING -> StepState.BLOCKED to "Pair this device with your Smara account"
                CapabilityState.PLANNED -> StepState.BLOCKED to "Adapter is reserved for the next build"
            }
            task = task.update(index, state, detail)
            emit(task)
        }

        val blocked = task.steps.any { it.state == StepState.BLOCKED }
        task = task.copy(
            state = if (blocked) TaskState.NEEDS_ATTENTION else TaskState.COMPLETED,
            answer = if (blocked) {
                "The plan is ready. Connect the requested capability to let Smara finish it safely."
            } else {
                "Done on this phone."
            },
        )
        emit(task)
    }

    private fun AgentTask.update(index: Int, state: StepState, detail: String): AgentTask = copy(
        steps = steps.mapIndexed { current, value ->
            if (current == index) value.copy(state = state, detail = detail) else value
        },
    )
}

