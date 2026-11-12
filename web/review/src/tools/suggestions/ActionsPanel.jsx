import { ActionButton, Bidi, formatCount, Kbd, Panel } from "@shell";
import { actionTargets, queuePlace, STRUCTURAL } from "./suggestions.js";

// The side column: the decision on the selected suggestion, or on the marked ones (each
// button says how many it moves). Only the actions that fit the suggestion's status are
// shown, each with its key; under them, what applying it will do. The footer applies every
// accepted suggestion. While a step rewrites a file the tool writes, every button is off and
// the panel says why.

function Locked({ lockedBy }) {
  return (
    <p className="note note-warning p-2 text-xs">
      <Bidi>{lockedBy.title}</Bidi> is running and rewriting a file this tool writes. You can read; deciding and applying
      open again when it finishes.
    </p>
  );
}

function ForMarked({ targets, act, disabled, clear }) {
  const count = (action) => actionTargets(targets, action).length;
  return (
    <div className="space-y-2">
      <p className="text-xs text-slate-600">
        {formatCount(targets.length)} marked.{" "}
        <button type="button" className="underline" onClick={clear}>
          Clear the marks
        </button>
      </p>
      <ActionButton label="Accept" keyName="a" count={count("accept")} disabled={disabled} onClick={() => act.decide("accept")} />
      <ActionButton label="Reject" keyName="x" count={count("reject")} disabled={disabled} onClick={() => act.decide("reject")} />
      <ActionButton label="Back to pending" keyName="o" count={count("reopen")} disabled={disabled} onClick={() => act.decide("reopen")} />
      <ActionButton label="Apply" keyName="y" count={count("apply")} primary disabled={disabled} onClick={() => act.apply()} />
    </div>
  );
}

function WhatApplyDoes({ item }) {
  if (item.status !== "pending" && item.status !== "accepted") return null;
  return (
    <p className="text-xs text-slate-500">
      {STRUCTURAL.includes(item.kind)
        ? "Applying changes the plan at once. Then steps 6b and 7 run (offered here) so the pages follow."
        : "Applying is one paid model call (a minute or two) that rewrites only this part of the page, in the background."}
    </p>
  );
}

function ForOne({ item, applies, act, disabled }) {
  const place = queuePlace(applies, item.id);
  const can = (action) => actionTargets([item], action).length > 0;
  const editable = (item.status === "pending" || item.status === "accepted") && !item.busy;
  return (
    <div className="space-y-2">
      {place && (
        <div className="note note-info space-y-1 p-2 text-xs">
          <p>{place === "running" ? "Being applied now, in the background. You can go on with other suggestions; the outcome shows here." : "Waiting in the apply queue."}</p>
          {place === "queued" && (
            <button type="button" className="btn btn-sm" onClick={() => act.withdraw(item.id)}>
              Take it out of the queue
            </button>
          )}
        </div>
      )}
      {can("accept") && <ActionButton label="Accept" keyName="a" primary disabled={disabled} onClick={() => act.decide("accept")} />}
      {can("apply") && (
        <ActionButton label={item.paid ? "Apply… (paid)" : "Apply"} keyName="y" primary disabled={disabled} onClick={() => act.apply()} />
      )}
      {editable && <ActionButton label="Correct the proposal…" keyName="e" disabled={disabled} onClick={() => act.edit()} />}
      {can("reject") && <ActionButton label="Reject" keyName="x" danger disabled={disabled} onClick={() => act.decide("reject")} />}
      {can("reopen") && <ActionButton label="Back to pending" keyName="o" disabled={disabled} onClick={() => act.decide("reopen")} />}
      <WhatApplyDoes item={item} />
      {item.status === "applied" && <p className="text-xs text-slate-500">Applied; this is final. What it changed is in the middle column.</p>}
    </div>
  );
}

export function ActionsPanel({ item, targets, marked, applies, accepted, act, lockedBy, saving, clear }) {
  const disabled = Boolean(lockedBy) || saving;
  return (
    <Panel
      title="Decide"
      footer={
        accepted.length > 0 && (
          <ActionButton label="Apply every accepted one" keyName="shift+y" count={accepted.length} disabled={disabled} onClick={() => act.applyAll()} />
        )
      }
    >
      <div className="space-y-4 p-4 text-sm">
        {lockedBy && <Locked lockedBy={lockedBy} />}
        {marked ? (
          <ForMarked targets={targets} act={act} disabled={disabled} clear={clear} />
        ) : item ? (
          <ForOne item={item} applies={applies} act={act} disabled={disabled} />
        ) : (
          <p className="text-slate-500">
            Pick a suggestion in the list (<Kbd>j</Kbd> / <Kbd>k</Kbd>), or mark several (<Kbd>Space</Kbd>) to decide on them together.
          </p>
        )}
      </div>
    </Panel>
  );
}
