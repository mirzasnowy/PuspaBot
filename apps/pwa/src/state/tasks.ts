/**
 * Tasks screen store: tasks created in this session plus the
 * approval queue seeded from the bootstrap. Approvals are decided
 * one tap at a time (see `approvals.ts`); a task's plan is the
 * stage/question returned by the create-task call.
 */
import type { TaskOutcome } from "../api/client";

export interface SessionTask {
  taskId: string;
  purpose: string;
  status: string;
  stage?: string;
  question?: string | null;
  stopped: boolean;
}

export interface TasksState {
  tasks: SessionTask[];
  creating: boolean;
  error: string | null;
}

export function initialTasksState(): TasksState {
  return { tasks: [], creating: false, error: null };
}

export function addTaskOutcome(state: TasksState, purpose: string, outcome: TaskOutcome): TasksState {
  const task: SessionTask = {
    taskId: outcome.task_id,
    purpose,
    status: outcome.status,
    stage: outcome.stage,
    question: outcome.question ?? null,
    stopped: false,
  };
  return { ...state, tasks: [task, ...state.tasks] };
}

export function markTaskStopped(state: TasksState, taskId: string): TasksState {
  return {
    ...state,
    tasks: state.tasks.map((t) =>
      t.taskId === taskId ? { ...t, stopped: true, status: "stopped" } : t,
    ),
  };
}

export function runningTasks(state: TasksState): SessionTask[] {
  return state.tasks.filter((t) => !t.stopped && t.status !== "completed" && t.status !== "failed");
}
