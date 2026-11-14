import { useEffect, useMemo } from "react";
import { BetweenMessages } from "./ConversationView.jsx";
import { formatCount } from "./format.js";
import { countText } from "./planText.js";
import { hiddenBetween, rangeNextTo, replyClosures, streamRows, threadEdges, windowEdges } from "./messageStream.js";
import { Divider, MessageRow, scrollToMessage, ThreadTag, WindowEdge } from "./MessageRow.jsx";

// The timeline view: the messages of every conversation shown, interleaved in chat order,
// each in its conversation's colour and letter. It makes the edges of what is shown
// visible: where chat messages are not shown, a line says how many, with buttons to load
// them (`onLoad([firstSeq, lastSeq])`; the tool fetches them as context), and the legend
// marks each conversation that may begin earlier or go on later than what is shown.
//
//   <TimelineView blocks={view.blocks} chatSize={view.chat_size} colors={colors}
//     selection={selection} currentId={id} onSelectThread={open} onLoad={addRange}
//     step={10} quietMinutes={25} inWindow={idsOrNull} window={{start, end} | null} />

function LoadButtons({ ranges, onLoad }) {
  return ranges.map(({ label, range }) =>
    range ? (
      <button key={label} type="button" className="btn btn-sm" onClick={() => onLoad(range)}>
        {label.replace("{n}", formatCount(range[1] - range[0] + 1))}
      </button>
    ) : null
  );
}

/** "N chat messages not shown" between two rows, with what can be loaded there. */
function HiddenGap({ previous, row, chatSize, step, onLoad }) {
  const before = previous?.message.seq ?? -1;
  const after = row?.message.seq ?? chatSize;
  const hidden = previous && row ? hiddenBetween(previous, row) : after - before - 1;
  if (hidden <= 0 || !onLoad) return null;
  const ranges =
    hidden <= step
      ? [{ label: "Show them", range: [before + 1, after - 1] }]
      : [
          previous && { label: "Show {n} after ↑", range: rangeNextTo(before, "after", step, chatSize, after) },
          row && { label: "Show {n} before ↓", range: rangeNextTo(after, "before", step, chatSize, before) },
        ].filter(Boolean);
  const where = !previous ? "earlier" : !row ? "later" : "in between";
  return (
    <Divider tone="text-sky-800">
      <span>
        {countText(hidden, "chat message")} {where} not shown
      </span>
      <LoadButtons ranges={ranges} onLoad={onLoad} />
    </Divider>
  );
}

function Legend({ blocks, colors, edges, currentId, onSelectThread, inWindow, onLoad, step, chatSize }) {
  return (
    <div className="flex shrink-0 flex-wrap gap-2 border-b border-slate-200 bg-white px-3 py-2 text-xs">
      {blocks.map((block) => {
        const edge = edges.get(block.thread_id);
        const inside = inWindow ? block.messages.filter((m) => inWindow.has(m.id)).length : null;
        return (
          <div
            key={block.thread_id}
            className={`flex items-center gap-1.5 rounded-md border px-1.5 py-1 ${block.thread_id === currentId ? "border-blue-500 ring-1 ring-blue-500" : "border-slate-200"}`}
          >
            <button type="button" className="flex items-center gap-1.5 text-slate-600 hover:underline" onClick={() => onSelectThread?.(block.thread_id)}
              title={`Open conversation ${block.thread_id}`}>
              <ThreadTag info={colors.get(block.thread_id)} threadId={block.thread_id} />
              {block.partial ? `${formatCount(block.messages.length)} of ${countText(block.size, "message")}` : countText(block.size, "message")}
              {inside !== null && `, ${formatCount(inside)} in the window`}
            </button>
            {edge?.start && (
              <button type="button" className="text-amber-800 hover:underline" title="The chat message right before its first shown one is not shown"
                onClick={() => onLoad?.(rangeNextTo(edge.first, "before", step, chatSize))}>
                may begin earlier ↑
              </button>
            )}
            {edge?.end && (
              <button type="button" className="text-amber-800 hover:underline" title="The chat message right after its last shown one is not shown"
                onClick={() => onLoad?.(rangeNextTo(edge.last, "after", step, chatSize))}>
                may go on later ↓
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function TimelineView({
  blocks, chatSize, colors, selection, currentId, onSelectThread, onLoad, step = 10, quietMinutes, inWindow, window, highlight,
}) {
  const rows = useMemo(() => streamRows(blocks), [blocks]);
  const closureOf = useMemo(() => replyClosures(rows), [rows]);
  const edges = useMemo(() => threadEdges(rows, chatSize), [rows, chatSize]);
  const crossings = useMemo(() => windowEdges(rows, inWindow), [rows, inWindow]);
  const anchor = window ? `window-${window.start}` : currentId;
  for (const block of blocks) colors.get(block.thread_id);

  useEffect(() => {
    requestAnimationFrame(() => {
      const target = document.getElementById("timeline-window-start") ||
        document.getElementById(`msg-${blocks.find((b) => b.thread_id === currentId)?.messages[0]?.id}`);
      target?.scrollIntoView({ block: "center" });
    });
    // Re-anchor only when the subject changes, not after every edit or load.
  }, [anchor, rows.length > 0]);

  if (!rows.length) return <div className="flex flex-1 items-center justify-center text-sm text-slate-500">No messages to show.</div>;
  const jump = (reply) => scrollToMessage(reply.id) || onLoad?.([reply.seq, reply.seq]);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Legend blocks={blocks} colors={colors} edges={edges} currentId={currentId} onSelectThread={onSelectThread}
        inWindow={inWindow} onLoad={onLoad} step={step} chatSize={chatSize} />
      <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-3 pb-10">
        <HiddenGap previous={null} row={rows[0]} chatSize={chatSize} step={step} onLoad={onLoad} />
        {rows.map((row, index) => {
          const previous = rows[index - 1];
          const crossing = crossings.get(index);
          return (
            <div key={row.key} className="space-y-1.5">
              {previous && <HiddenGap previous={previous} row={row} chatSize={chatSize} step={step} onLoad={onLoad} />}
              {crossing !== undefined && <WindowEdge entering={crossing} window={window} id={crossing ? "timeline-window-start" : undefined} />}
              <BetweenMessages previous={previous} row={row} quietMinutes={quietMinutes} countHidden={false} />
              <MessageRow
                message={row.message}
                info={colors.get(row.threadId)}
                colors={colors}
                selected={selection?.selected.has(row.key) ?? false}
                onToggle={selection && ((shift) => selection.click(rows, index, closureOf, shift))}
                outside={inWindow ? !inWindow.has(row.key) : false}
                showTag
                highlight={highlight}
                onJump={jump}
              />
            </div>
          );
        })}
        <HiddenGap previous={rows[rows.length - 1]} row={null} chatSize={chatSize} step={step} onLoad={onLoad} />
      </div>
    </div>
  );
}
