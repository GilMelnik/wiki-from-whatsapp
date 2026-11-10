import { useEffect, useMemo, useState } from "react";
import GoldDivider from "../components/GoldDivider";
import { formatMinute } from "../goldWindows";
import { chronoStream, windowEdges } from "./chronoStream.js";
import QuoteBlock from "./QuoteBlock";
import { groupByThread, quoteClosures, selectionRules } from "./selection.js";
import ThreadTag from "./ThreadTag";

/**
 * The messages of `threads` in one time-ordered stream, each row in its thread's colour.
 *
 * The legend names every thread (click: make it current). While messages are selected it
 * also offers "move here" per thread and "new thread"; `onMove(keys, targetId | null)` does
 * the move (a promise; the buttons stay disabled until it settles). `inWindowIds` /
 * `goldSpan` add the gold-window marks (optional).
 */
export default function ChronoViewer({
  threads,
  colors,
  currentId,
  onSelectThread,
  selection,
  onMove,
  inWindowIds,
  goldSpan,
}) {
  const rows = useMemo(() => chronoStream(threads), [threads]);
  const closureOf = useMemo(() => quoteClosures(rows), [rows]);
  const inWindow = useMemo(() => (inWindowIds ? new Set(inWindowIds) : null), [inWindowIds]);
  const edges = useMemo(() => windowEdges(rows, inWindow), [rows, inWindow]);
  const distinct = useMemo(() => {
    const seen = new Map();
    for (const thread of threads) if (thread && !seen.has(thread.thread_id)) seen.set(thread.thread_id, thread);
    return [...seen.values()];
  }, [threads]);
  const sizes = useMemo(
    () => new Map(distinct.map((t) => [t.thread_id, t.messages?.length || 0])),
    [distinct]
  );
  const [busy, setBusy] = useState(false);
  const groups = useMemo(() => groupByThread(rows, selection.selected), [rows, selection.selected]);
  const rules = selectionRules(groups, sizes);
  const anchor = goldSpan ? `gold-${goldSpan.start}` : currentId;

  useEffect(() => {
    requestAnimationFrame(() => {
      const target =
        document.getElementById("chrono-gold-start") ||
        document.getElementById(`chrono-first-${currentId}`);
      target?.scrollIntoView({ block: "center" });
    });
    // Re-anchor only when the stream's subject changes, not after every edit.
  }, [anchor, rows.length > 0]);

  if (!rows.length) {
    return (
      <div className="flex items-center justify-center flex-1 min-h-0 text-slate-400">
        {threads.length ? "אין הודעות להצגה" : "בחרו שיחה מהרשימה"}
      </div>
    );
  }

  // Only what is on screen moves, even if the selection kept keys of threads no longer shown.
  const move = async (targetId) => {
    setBusy(true);
    try {
      await onMove([...groups.values()].flatMap((g) => g.keys), targetId);
    } finally {
      setBusy(false);
    }
  };
  const firstOfThread = new Set();

  return (
    <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
      <div className="px-4 py-2 border-b bg-white text-sm shrink-0 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold">תצוגה כרונולוגית</span>
          <span className="text-slate-500">
            {rows.length} הודעות · {distinct.length} שיחות
            {goldSpan && ` · חלון ${formatMinute(goldSpan.start)}–${formatMinute(goldSpan.end).slice(11)}`}
          </span>
          {rules.count > 0 && (
            <span className="text-amber-700">
              {rules.count} נבחרו מ-{rules.threads.length} שיחות — בחרו יעד:
            </span>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {distinct.map((thread) => {
            const id = thread.thread_id;
            const info = colors.get(id);
            const reason = rules.moveTo(id);
            const inside = inWindow ? thread.messages.filter((m) => inWindow.has(m.id)).length : null;
            return (
              <div
                key={id}
                className={`flex items-stretch rounded border text-xs overflow-hidden ${
                  id === currentId ? "ring-2 ring-blue-500" : ""
                }`}
                style={{ borderColor: info.color }}
              >
                <button
                  type="button"
                  onClick={() => onSelectThread(id)}
                  title="הפוך לשיחה הנוכחית"
                  className="flex items-center gap-1 px-2 py-1 bg-white hover:bg-slate-50"
                >
                  <ThreadTag info={info} />
                  <span className="font-mono">{id}</span>
                  <span className="text-slate-500">
                    {thread.messages.length}
                    {inside != null && ` · ${inside} בחלון`}
                  </span>
                </button>
                {rules.count > 0 && (
                  <button
                    type="button"
                    disabled={busy || Boolean(reason)}
                    title={reason || `העבר ${rules.count} הודעות ל-${id}`}
                    onClick={() => move(id)}
                    className="px-2 py-1 font-medium border-r disabled:opacity-40"
                    style={{ backgroundColor: info.color, color: info.ink, borderColor: info.color }}
                  >
                    העבר לכאן
                  </button>
                )}
              </div>
            );
          })}
          {rules.count > 0 && (
            <button
              type="button"
              disabled={busy || Boolean(rules.newThread)}
              title={rules.newThread || `העבר ${rules.count} הודעות לשיחה חדשה`}
              onClick={() => move(null)}
              className="px-2 py-1 text-xs rounded border border-dashed border-slate-500 bg-white hover:bg-slate-100 disabled:opacity-40"
            >
              + שיחה חדשה
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain p-3 pb-8 space-y-2">
        {rows.map((row, index) => {
          const info = colors.get(row.threadId);
          const selected = selection.selected.has(row.key);
          const inside = inWindow ? inWindow.has(row.key) : null;
          const edge = edges.get(index);
          const first = !firstOfThread.has(row.threadId);
          firstOfThread.add(row.threadId);
          const msg = row.message;
          return (
            <div key={row.key}>
              {edge !== undefined && (
                <GoldDivider id={edge ? "chrono-gold-start" : undefined} entering={edge} gold={goldSpan} />
              )}
              <div
                id={first ? `chrono-first-${row.threadId}` : undefined}
                role="button"
                tabIndex={0}
                onClick={(e) => selection.click(rows, index, closureOf, e.shiftKey)}
                onKeyDown={(e) => {
                  if (e.key === " " || e.key === "Enter") {
                    e.preventDefault();
                    selection.click(rows, index, closureOf, e.shiftKey);
                  }
                }}
                className={`chrono-msg flex gap-2 p-2 rounded border select-none cursor-pointer ${
                  selected ? "message-selected" : ""
                } ${inside === null ? "" : inside ? "gold-in" : "gold-out"}`}
                style={{
                  borderRightWidth: 6,
                  borderRightColor: info.color,
                  backgroundColor: selected ? undefined : `${info.color}1a`,
                }}
              >
                <input
                  type="checkbox"
                  checked={selected}
                  readOnly
                  tabIndex={-1}
                  className="mt-1 shrink-0 pointer-events-none"
                />
                <div className="min-w-0 flex-1">
                  <div className="text-xs text-slate-600 mb-1 flex flex-wrap items-center gap-x-2">
                    <ThreadTag info={info} title={row.threadId} />
                    <span>
                      [m{row.position}] {formatMinute(msg.datetime)} · {msg.sender}
                    </span>
                  </div>
                  {msg.quote && <QuoteBlock quote={msg.quote} quotedIndex={null} />}
                  <div dir="auto" className="text-sm whitespace-pre-wrap break-words">
                    {msg.content || "(ללא תוכן)"}
                  </div>
                  {msg.reactions?.length > 0 && (
                    <div className="text-xs text-slate-400 mt-1">
                      {msg.reactions.map((r) => r.emoji).join(" ")}
                    </div>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
