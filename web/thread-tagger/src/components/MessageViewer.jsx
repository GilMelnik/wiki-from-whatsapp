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

function buildDisplaySections(thread, context) {
  const sections = [];
  const prev = context?.prev;
  const next = context?.next;
  const family = context?.family || [];
  const currentId = thread?.thread_id;
  const splitFamily = family.length > 1;

  if (prev?.messages?.length) {
    sections.push({
      threadId: prev.thread_id,
      isCurrent: false,
      position: "prev",
      messages: prev.messages,
    });
  }

  if (splitFamily) {
    for (const part of family) {
      sections.push({
        threadId: part.thread_id,
        isCurrent: part.thread_id === currentId,
        position: part.thread_id === currentId ? "current" : "split",
        messages: part.messages || [],
      });
    }
  } else if (thread) {
    sections.push({
      threadId: thread.thread_id,
      isCurrent: true,
      position: "current",
      messages: thread.messages || [],
    });
  }

  if (next?.messages?.length) {
    sections.push({
      threadId: next.thread_id,
      isCurrent: false,
      position: "next",
      messages: next.messages,
    });
  }

  return sections;
}

function sectionLabel(section) {
  if (section.position === "prev") return "שיחה קודמת בכרונולוגיה";
  if (section.position === "next") return "שיחה הבאה בכרונולוגיה";
  if (section.position === "split") return "חלק מפיצול";
  return "שיחה נוכחית";
}

const SECTION_WRAPPER = {
  current: "thread-section-current",
  prev: "thread-section-prev",
  next: "thread-section-next",
  split: "thread-section-split",
};

const SECTION_HEADER = {
  current: "thread-header-current",
  prev: "thread-header-prev",
  next: "thread-header-next",
  split: "thread-header-split",
};

const SECTION_MESSAGE = {
  current: "thread-msg-current",
  prev: "thread-msg-prev",
  next: "thread-msg-next",
  split: "thread-msg-split",
};

function sectionWrapperClass(section) {
  if (section.isCurrent) return SECTION_WRAPPER.current;
  return SECTION_WRAPPER[section.position] || SECTION_WRAPPER.split;
}

function sectionHeaderClass(section) {
  if (section.isCurrent) return SECTION_HEADER.current;
  return SECTION_HEADER[section.position] || SECTION_HEADER.split;
}

function messageClass(section, selected) {
  if (section.isCurrent) {
    return selected ? "message-selected border-r-[5px] border-r-amber-600" : SECTION_MESSAGE.current;
  }
  return SECTION_MESSAGE[section.position] || SECTION_MESSAGE.split;
}

export default function MessageViewer({
  thread,
  context,
  selectedIndices,
  onToggle,
  onRangeSelect,
  onNavigateToThread,
  isSplitPart,
}) {
  const scrollRef = useRef(null);
  const activeThreadIdRef = useRef(null);
  const anchorScrolledRef = useRef(null);

  const sections = useMemo(
    () => buildDisplaySections(thread, context),
    [thread, context]
  );

  const currentSection = useMemo(
    () => sections.find((s) => s.isCurrent),
    [sections]
  );

  const messages = currentSection?.messages || thread?.messages || [];

  const quotedBy = useMemo(() => {
    const { quotedBy: map } = buildQuoteGraph(messages);
    return map;
  }, [messages]);

  const jumpToQuote = useCallback(
    (index) => {
      const el = document.getElementById(`msg-${thread?.thread_id}-${index}`);
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
      el?.classList.add("ring-2", "ring-blue-400");
      setTimeout(() => el?.classList.remove("ring-2", "ring-blue-400"), 1500);
    },
    [thread?.thread_id]
  );

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
    const hasSaved = saved != null;
    anchorScrolledRef.current = null;

    requestAnimationFrame(() => {
      if (!scrollRef.current || activeThreadIdRef.current !== id) return;
      if (hasSaved) {
        scrollRef.current.scrollTop = saved;
        return;
      }
      const anchor = document.getElementById(`thread-anchor-${id}`);
      if (anchor) {
        anchor.scrollIntoView({ block: "start" });
        anchorScrolledRef.current = id;
      }
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

  const hasContext = sections.some((s) => !s.isCurrent);

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
        {hasContext && (
          <span className="text-xs text-slate-500">
            גלילה מציגה שיחות סמוכות וחלקי פיצול
          </span>
        )}
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
        {sections.map((section) => (
          <div key={section.threadId} className={`space-y-2 ${sectionWrapperClass(section)}`}>
            <div
              id={section.isCurrent ? `thread-anchor-${section.threadId}` : undefined}
              className={`sticky top-0 z-10 flex items-center gap-2 px-2 py-1.5 rounded border-2 text-xs ${sectionHeaderClass(section)}`}
            >
              <span className="font-medium">{sectionLabel(section)}</span>
              <span className="font-mono">{section.threadId}</span>
              <span className="text-slate-500">
                {section.messages.length} הודעות
              </span>
              {!section.isCurrent && onNavigateToThread && (
                <button
                  type="button"
                  onClick={() => onNavigateToThread(section.threadId)}
                  className="mr-auto text-blue-600 hover:underline"
                >
                  פתח שיחה
                </button>
              )}
            </div>

            {section.messages.map((msg, index) => {
              const selected = section.isCurrent && selectedIndices.has(index);
              const quotedIndex =
                section.isCurrent && msg.quote
                  ? findQuotedIndex(section.messages, msg.quote, index)
                  : null;
              const quotedByIndices = section.isCurrent
                ? quotedBy.get(index) || []
                : [];
              const msgKey = `${section.threadId}-${msg.id || index}-${msg.datetime}`;

              const baseClasses = messageClass(section, selected);

              return (
                <div
                  key={msgKey}
                  id={
                    section.isCurrent
                      ? `msg-${section.threadId}-${index}`
                      : undefined
                  }
                  role={section.isCurrent ? "button" : undefined}
                  tabIndex={section.isCurrent ? 0 : undefined}
                  className={`flex gap-2 p-2 rounded border transition-shadow select-none ${baseClasses}`}
                  onClick={(e) => {
                    if (e.target.closest("button")) return;
                    if (section.isCurrent) {
                      activate(index, e.shiftKey);
                      return;
                    }
                    onNavigateToThread?.(section.threadId);
                  }}
                  onKeyDown={
                    section.isCurrent
                      ? (e) => {
                          if (e.key === " " || e.key === "Enter") {
                            e.preventDefault();
                            activate(index, e.shiftKey);
                          }
                        }
                      : undefined
                  }
                >
                  {section.isCurrent ? (
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
                  ) : (
                    <div className="mt-1 w-4 shrink-0" aria-hidden />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="text-xs text-slate-500 mb-1 flex flex-wrap gap-x-2">
                      <span>
                        [m{index}] {formatTime(msg.datetime)} · {msg.sender}
                      </span>
                      {quotedByIndices.length > 0 && (
                        <span className="text-blue-600">
                          ↩ צוטט ב-[m{quotedByIndices.join(", m")}]
                        </span>
                      )}
                    </div>
                    {msg.quote && section.isCurrent && (
                      <QuoteBlock
                        quote={msg.quote}
                        quotedIndex={quotedIndex}
                        onJumpToQuote={jumpToQuote}
                      />
                    )}
                    {msg.quote && !section.isCurrent && (
                      <QuoteBlock quote={msg.quote} quotedIndex={null} />
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
        ))}
      </div>
    </div>
  );
}
