import { describe, expect, it } from "vitest";
import { addTaskOutcome, initialTasksState, markTaskStopped, runningTasks } from "./tasks";

describe("tasks store", () => {
  it("adds a task outcome and lists running tasks", () => {
    const state = addTaskOutcome(initialTasksState(), "riset", {
      task_id: "t-1",
      status: "completed",
      stage: "done",
    });
    expect(state.tasks).toHaveLength(1);
    expect(state.tasks[0].purpose).toBe("riset");
    expect(runningTasks(state)).toHaveLength(0);
  });

  it("marks a task stopped", () => {
    const state = addTaskOutcome(initialTasksState(), "riset", {
      task_id: "t-1",
      status: "running",
    });
    expect(runningTasks(state)).toHaveLength(1);
    const stopped = markTaskStopped(state, "t-1");
    expect(stopped.tasks[0].stopped).toBe(true);
    expect(runningTasks(stopped)).toHaveLength(0);
  });
});
