import { useCallback, useEffect, useMemo, useRef } from "react";
import {
  buildQuoteGraph,
  findQuotedIndex,
} from "../quoteGraph";

/** Per-thread scroll positions when switching between threads. */
const threadScrollPositions = new Map();

function formatTime(iso) {
  if (!iso) return "";
  return iso.replace("T", " ").slice(0, 16);
}

function QuoteBlock({ quote, quotedIndex, onJumpToQuote }) {
  return (
    <div className="mb-2 pr-3 border-r-4 border-slate-300 bg-slate-50 rounded-r px-2 py-1.5 text-xs">
      <div className="text-slate-500 mb-0.5 flex items-center gap-2 flex-wrap">
        <span className="font-medium">ציטוט</span>
        {quote.sender && <span>· {quote.sender}</span>}
        {quotedIndex != null && onJumpToQuote && (
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onJumpToQuote(quotedIndex);
            }}
            className="text-blue-600 hover:underline"
          >
            → [m{quotedIndex}]
          </button>
        )}
      </div>
      <div className="text-slate-600 whitespace-pre-wrap break-words line-clamp-4">
        {quote.text}
      </div>
    </div>
  );
}

export default function MessageViewer({
  thread,
  selectedIndices,
  onToggle,
  onRangeSelect,
  isSplitPart,
}) {
  const messages = thread?.messages || [];
  const scrollRef = useRef(null);
  const activeThreadIdRef = useRef(null);

  const quotedBy = useMemo(() => {
    const { quotedBy: map } = buildQuoteGraph(messages);
    return map;
  }, [messages]);

  const jumpToQuote = useCallback((index) => {
    const el = document.getElementById(`msg-${thread?.thread_id}-${index}`);
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
    el?.classList.add("ring-2", "ring-blue-400");
    setTimeout(() => el?.classList.remove("ring-2", "ring-blue-400"), 1500);
  }, [thread?.thread_id]);

  const persistScroll = useCallback(() => {
    const id = activeThreadIdRef.current;
    const scrollEl = scrollRef.current;
    if (id && scrollEl) {
      threadScrollPositions.set(id, scrollEl.scrollTop);
    }
  }, []);

  useEffect(() => {
    const scrollEl = scrollRef.current;
    const id = thread?.thread_id;
    if (!scrollEl || !id) return;

    const prevId = activeThreadIdRef.current;
    if (prevId && prevId !== id) {
      threadScrollPositions.set(prevId, scrollEl.scrollTop);
    }
    activeThreadIdRef.current = id;

    const saved = threadScrollPositions.get(id);
    requestAnimationFrame(() => {
      if (!scrollRef.current || activeThreadIdRef.current !== id) return;
      scrollRef.current.scrollTop = saved ?? 0;
    });
  }, [thread?.thread_id, messages.length]);

  const activate = useCallback(
    (index, shiftKey) => {
      if (shiftKey && selectedIndices.size > 0) {
        const anchor = Math.min(...selectedIndices);
        onRangeSelect(Math.min(anchor, index), Math.max(anchor, index));
        return;
      }
      onToggle(index);
    },
    [selectedIndices, onToggle, onRangeSelect]
  );

  if (!thread) {
    return (
      <div className="flex items-center justify-center flex-1 min-h-0 text-slate-400">
        בחרו שיחה מהרשימה
      </div>
    );
  }

  return (
    <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
      <div className="px-4 py-2 border-b bg-white flex flex-wrap gap-3 text-sm items-center shrink-0">
        <span className="font-semibold">{thread.thread_id}</span>
        {isSplitPart && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-violet-100 text-violet-800 border border-violet-200">
            חלק מפיצול
          </span>
        )}
        {thread.thread_id.includes("-split-") && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800 border border-emerald-200">
            שיחה חדשה
          </span>
        )}
        <span>{thread.num_messages} הודעות</span>
        <span>{thread.num_unique_senders} משתתפים</span>
        <span>
          {formatTime(thread.start_time)} — {formatTime(thread.last_time)}
        </span>
        {selectedIndices.size > 0 && (
          <span className="text-amber-700">
            {selectedIndices.size} נבחרו (כולל שרשרת ציטוטים)
          </span>
        )}
      </div>
      <div
        ref={scrollRef}
        onScroll={persistScroll}
        className="flex-1 min-h-0 overflow-y-auto overscroll-contain p-3 pb-8 space-y-2"
      >
        {messages.map((msg, index) => {
          const selected = selectedIndices.has(index);
          const quotedIndex = msg.quote
            ? findQuotedIndex(messages, msg.quote, index)
            : null;
          const quotedByIndices = quotedBy.get(index) || [];

          return (
            <div
              key={`${msg.id || index}-${msg.datetime}`}
              id={`msg-${thread.thread_id}-${index}`}
              role="button"
              tabIndex={0}
              className={`flex gap-2 p-2 rounded border cursor-pointer transition-shadow select-none ${
                selected ? "message-selected" : "bg-white border-slate-200 hover:bg-slate-50"
              }`}
              onClick={(e) => {
                if (e.target.closest("button")) return;
                activate(index, e.shiftKey);
              }}
              onKeyDown={(e) => {
                if (e.key === " " || e.key === "Enter") {
                  e.preventDefault();
                  activate(index, e.shiftKey);
                }
              }}
            >
              <input
                type="checkbox"
                checked={selected}
                readOnly={false}
                tabIndex={-1}
                className="mt-1 shrink-0 cursor-pointer"
                onClick={(e) => e.stopPropagation()}
                onChange={(e) => {
                  e.stopPropagation();
                  activate(index, e.nativeEvent.shiftKey);
                }}
              />
              <div className="min-w-0 flex-1">
                <div className="text-xs text-slate-500 mb-1 flex flex-wrap gap-x-2">
                  <span>[m{index}] {formatTime(msg.datetime)} · {msg.sender}</span>
                  {quotedByIndices.length > 0 && (
                    <span className="text-blue-600">
                      ↩ צוטט ב-[m{quotedByIndices.join(", m")}]
                    </span>
                  )}
                </div>
                {msg.quote && (
                  <QuoteBlock
                    quote={msg.quote}
                    quotedIndex={quotedIndex}
                    onJumpToQuote={jumpToQuote}
                  />
                )}
                <div className="text-sm whitespace-pre-wrap break-words">
                  {msg.content || "(ללא תוכן)"}
                </div>
                {msg.reactions?.length > 0 && (
                  <div className="text-xs text-slate-400 mt-1">
                    {msg.reactions.map((r) => r.emoji).join(" ")}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
