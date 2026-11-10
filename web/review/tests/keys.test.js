import assert from "node:assert/strict";
import { test } from "node:test";
import { comboFromEvent, isTypingTarget, matchBinding } from "../src/shell/keys.js";

test("letters follow the physical key, so shortcuts work on the Hebrew layout", () => {
  assert.equal(comboFromEvent({ code: "KeyJ", key: "ח" }), "j");
  assert.equal(comboFromEvent({ code: "KeyS", key: "ד", ctrlKey: true }), "mod+s");
  assert.equal(comboFromEvent({ code: "KeyJ", key: "J", shiftKey: true }), "shift+j");
  assert.equal(comboFromEvent({ code: "Digit1", key: "1", altKey: true }), "alt+1");
});

test("symbols keep their own shift and named keys are lowercase", () => {
  assert.equal(comboFromEvent({ code: "Slash", key: "?", shiftKey: true }), "?");
  assert.equal(comboFromEvent({ code: "ArrowDown", key: "ArrowDown" }), "arrowdown");
  assert.equal(comboFromEvent({ code: "Enter", key: "Enter", shiftKey: true }), "shift+enter");
});

test("plain keys are ignored while typing unless the binding opts in", () => {
  const bindings = [
    { keys: "a", run: () => "accept" },
    { keys: ["mod+s"], run: () => "save" },
    { keys: "enter", allowInInput: true, run: () => "submit" },
  ];
  assert.equal(matchBinding(bindings, "a", false), bindings[0]);
  assert.equal(matchBinding(bindings, "a", true), undefined);
  assert.equal(matchBinding(bindings, "mod+s", true), bindings[1]);
  assert.equal(matchBinding(bindings, "enter", true), bindings[2]);
  assert.equal(isTypingTarget({ tagName: "TEXTAREA" }), true);
  assert.equal(isTypingTarget({ tagName: "DIV", isContentEditable: true }), true);
  assert.equal(isTypingTarget({ tagName: "BUTTON" }), false);
});
