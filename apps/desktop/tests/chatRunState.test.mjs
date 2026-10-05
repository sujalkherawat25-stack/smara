import test from "node:test";
import assert from "node:assert/strict";
import { elapsedLabel, matchesChatRequest, runIsQuiet } from "../src/chatRunState.ts";

test("events from an old turn cannot finish a newer turn in the same conversation", () => {
  assert.equal(matchesChatRequest({ type: "done", session_id: "chat", request_id: "old" }, "chat", "new"), false);
  assert.equal(matchesChatRequest({ type: "error", session_id: "chat", request_id: "old" }, "chat", "new"), false);
  assert.equal(matchesChatRequest({ type: "token", session_id: "chat", request_id: "new" }, "chat", "new"), true);
});

test("events from another conversation are ignored", () => {
  assert.equal(matchesChatRequest({ session_id: "other" }, "chat", "turn"), false);
  assert.equal(matchesChatRequest({ type: "status" }, "chat", "turn"), true);
});

test("elapsed time is readable and never negative", () => {
  assert.equal(elapsedLabel(0), "0:00");
  assert.equal(elapsedLabel(125_999), "2:05");
  assert.equal(elapsedLabel(-500), "0:00");
});

test("quiet runs are distinguished from waiting for the user", () => {
  assert.equal(runIsQuiet(31_000, 0, "Waiting for model response"), true);
  assert.equal(runIsQuiet(31_000, 10_000, "Waiting for model response"), false);
  assert.equal(runIsQuiet(310_000, 0, "Waiting for your approval"), false);
});
