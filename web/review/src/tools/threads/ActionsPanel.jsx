import { Bidi, countText, formatSpan, Kbd, Ltr, Panel, ThreadTag } from "@shell";

// The side column: what can be done with the selected messages (move them to a conversation
// in view, make them a new conversation, split the conversation at one message) and with the
// opened conversation (merge it with another), plus undo. Only what fits the selection is
// shown, each with its key. While step 1 runs every button is off and the panel says why.

function ActionButton({ label, keyName, disabled, title, primary = false, onClick, children }) {
  return (
    <button type="button" className={`btn btn-block justify-between text-start ${primary ? "btn-primary" : ""}`} disabled={disabled} title={title} onClick={onClick}>
      <span className="flex min-w-0 items-center gap-1.5">{children ?? label}</span>
      {keyName && <Kbd>{keyName}</Kbd>}
    </button>
  );
}

function Targets({ targets, colors, disabled, onMove }) {
  return (
    <div className="space-y-1.5">
      <h3 className="section-title">Move them to</h3>
      {targets.map(({ threadId, block, reason }, index) => (
        <ActionButton key={threadId} keyName={index < 9 ? String(index + 1) : null} disabled={disabled || Boolean(reason)} title={reason ?? undefined} onClick={() => onMove(threadId)}>
          <ThreadTag info={colors.peek(threadId)} threadId={threadId} />
          <span className="min-w-0 truncate">
            <Ltr>{threadId}</Ltr> <span className="text-xs opacity-75">{formatSpan(block.start_time, block.last_time)}</span>
          </span>
        </ActionButton>
      ))}
    </div>
  );
}

function Selected({ rules, colors }) {
  return (
    <p className="flex flex-wrap items-center gap-1 text-slate-700">
      {countText(rules.count, "message")} selected, from
      {rules.threads.map((id) => (
        <ThreadTag key={id} info={colors.peek(id)} threadId={id} />
      ))}
    </p>
  );
}

export function ActionsPanel({ rules, targets, rest, colors, current, undo, locked, busy, act }) {
  const disabled = Boolean(locked) || busy;
  return (
    <Panel
      title="Change"
      footer={
        undo && (
          <ActionButton keyName="u" disabled={disabled} onClick={act.undo} title={`Made ${undo.at}`}>
            <span className="truncate">Undo: {undo.label}</span>
          </ActionButton>
        )
      }
    >
      <div className="space-y-5 p-4 text-sm">
        {locked && (
          <p className="note note-warning p-2 text-xs">
            <Bidi>{locked.title}</Bidi> is running and rewriting the conversations. You can read; changes open again when it finishes.
          </p>
        )}
        {rules.count === 0 ? (
          <p className="text-slate-600">
            Select the messages that belong elsewhere: click a message (the messages it replies to or that reply to it come
            along), <Kbd>Shift</Kbd>-click for a range. Then move them, or make them a conversation of their own.
          </p>
        ) : (
          <div className="space-y-3">
            <Selected rules={rules} colors={colors} />
            <ActionButton label="New conversation from them" keyName="n" primary disabled={disabled || Boolean(rules.newThread)} title={rules.newThread ?? undefined} onClick={act.newThread} />
            {rules.count === 1 && (
              <ActionButton label="New conversation from here to its end" keyName="s" disabled={disabled || Boolean(rest.reason)} title={rest.reason} onClick={act.splitHere} />
            )}
            <Targets targets={targets} colors={colors} disabled={disabled} onMove={act.move} />
          </div>
        )}
        {current && (
          <div className="space-y-1.5">
            <h3 className="section-title">The opened conversation</h3>
            <p className="flex items-center gap-1.5 text-xs text-slate-600">
              <ThreadTag info={colors.peek(current)} threadId={current} /> <Ltr>{current}</Ltr>
            </p>
            <ActionButton label="Merge it with another…" keyName="m" disabled={disabled} onClick={act.openMerge} />
          </div>
        )}
      </div>
    </Panel>
  );
}
