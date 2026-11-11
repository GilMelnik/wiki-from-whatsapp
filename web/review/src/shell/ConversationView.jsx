import { useEffect, useMemo } from "react";
import { formatCount, formatDay, formatSpan } from "./format.js";
import { countText } from "./planText.js";
import { blockRows, formatPause, hiddenBetween, minutesBetween, replyClosures, windowEdges } from "./messageStream.js";
import { Divider, MessageRow, scrollToMessage, ThreadTag, WindowEdge } from "./MessageRow.jsx";
import { Ltr } from "./text.jsx";

// The conversation view: one section per conversation (the opened one between the ones the
// chat had right before and after it), each message in its conversation's colour. Between
// two messages of a section it shows what the decision "does this still belong here" rests
// on: a new day, a pause longer than step 1's rule (`quietMinutes`), and how many chat
// messages of other conversations were written in between.
//
//   <ConversationView blocks={view.blocks} colors={colors} selection={selection}
//     currentId={id} onSelectThread={open} quietMinutes={25} highlight={q}
//     inWindow={idsOrNull} window={{start, end} | null} />
//
// Without `selection` the view is read-only (a tool that only shows a thread).

export const ROLE_LABELS = {
  previous: "Before it, in the chat",
  around: "Before and after it, in the chat",
  current: "This conversation",
  part: "Another part of the same split",
  next: "After it, in the chat",
  window: "In the window",
  included: "Shown on request",
  context: "Loaded context",
};

function SectionHeader({ block, info, current, onSelectThread }) {
  const first = block.messages[0]?.datetime;
  const last = block.messages[block.messages.length - 1]?.datetime;
  return (
    <div className={`sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-md border border-slate-200 px-2 py-1.5 text-xs ${current ? "bg-white" : "bg-slate-100"}`}>
      <ThreadTag info={info} threadId={block.thread_id} />
      <span className="font-semibold text-slate-700">{ROLE_LABELS[block.role] ?? block.role}</span>
      <Ltr className="text-slate-500">{block.thread_id}</Ltr>
      <span className="text-slate-500">
        {first && formatSpan(first, last)} · {countText(block.size, "message")}
        {block.partial && `, ${formatCount(block.messages.length)} shown`}
      </span>
      {!current && onSelectThread && (
        <button type="button" className="btn btn-sm ms-auto" onClick={() => onSelectThread(block.thread_id)}>
          Open
        </button>
      )}
    </div>
  );
}

/** What lies between two consecutive messages: a new day, a long pause, and (when
 * `countHidden`) how many chat messages of other conversations were written in between. */
export function BetweenMessages({ previous, row, quietMinutes, countHidden = true }) {
  if (!previous) return null;
  const pause = minutesBetween(previous.message.datetime, row.message.datetime);
  const hidden = countHidden ? hiddenBetween(previous, row) : 0;
  const newDay = previous.message.datetime.slice(0, 10) !== row.message.datetime.slice(0, 10);
  if (!newDay && pause < quietMinutes && hidden === 0) return null;
  const long = pause >= quietMinutes;
  return (
    <Divider tone={long ? "text-amber-800" : "text-slate-500"}>
      {newDay && <span className="font-semibold">{formatDay(row.message.datetime)}</span>}
      {long && (
        <span title={`Step 1 keeps messages up to ${quietMinutes} min apart in one conversation`}>{formatPause(pause)} later</span>
      )}
      {hidden > 0 && <span>{countText(hidden, "chat message")} of other conversations in between</span>}
    </Divider>
  );
}

export function ConversationView({ blocks, colors, selection, currentId, onSelectThread, quietMinutes, inWindow, window, highlight }) {
  const rows = useMemo(() => blocks.flatMap(blockRows), [blocks]);
  const closureOf = useMemo(() => replyClosures(rows), [rows]);
  const index = useMemo(() => new Map(rows.map((row, i) => [row.key, i])), [rows]);
  for (const block of blocks) colors.get(block.thread_id);

  // Opening a conversation shows its start (in a gold window: its first message inside it).
  useEffect(() => {
    const messages = blocks.find((block) => block.thread_id === currentId)?.messages ?? [];
    const first = messages.find((message) => inWindow?.has(message.id)) ?? messages[0];
    if (first) requestAnimationFrame(() => document.getElementById(`msg-${first.id}`)?.scrollIntoView({ block: "center" }));
  }, [currentId, blocks.length > 0]);

  const jump = (reply) => scrollToMessage(reply.id) || onSelectThread?.(reply.thread_id);
  return (
    <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3 pb-10">
      {blocks.map((block) => {
        const info = colors.get(block.thread_id);
        const own = blockRows(block);
        const crossings = windowEdges(own, inWindow);
        return (
          <section key={block.thread_id} className="space-y-1.5">
            <SectionHeader block={block} info={info} current={block.thread_id === currentId} onSelectThread={onSelectThread} />
            {own.map((row, i) => (
              <div key={row.key} className="space-y-1.5">
                <BetweenMessages previous={own[i - 1]} row={row} quietMinutes={quietMinutes} />
                {crossings.has(i) && <WindowEdge entering={crossings.get(i)} window={window} />}
                <MessageRow
                  message={row.message}
                  info={info}
                  colors={colors}
                  selected={selection?.selected.has(row.key) ?? false}
                  onToggle={selection && ((shift) => selection.click(rows, index.get(row.key), closureOf, shift))}
                  outside={inWindow ? !inWindow.has(row.key) : false}
                  highlight={highlight}
                  onJump={jump}
                />
              </div>
            ))}
          </section>
        );
      })}
    </div>
  );
}
