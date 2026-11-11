import { formatDateTime, formatTime } from "./format.js";
import { Bidi, Ltr } from "./text.jsx";

// The pieces of the shared message views: one message, the conversation chip, and the thin
// dividers between messages (a new day, a pause, chat messages not shown, a window edge).

/** The letter chip that names a conversation (colour + letter; the id in the tooltip). */
export function ThreadTag({ info, threadId, className = "" }) {
  if (!info) return null;
  return (
    <span
      title={threadId}
      className={`inline-flex min-w-[1.25rem] items-center justify-center rounded px-1 py-0.5 text-[11px] font-bold leading-none ${info.chip} ${className}`}
    >
      {info.tag}
    </span>
  );
}

/** A labelled line across the stream; `tone` is a text colour class. */
export function Divider({ tone = "text-slate-500", children, id }) {
  return (
    <div id={id} className={`flex items-center gap-2 py-1 text-xs ${tone}`}>
      <span className="flex-1 border-t border-dashed border-current opacity-40" />
      <span className="flex flex-wrap items-center gap-2">{children}</span>
      <span className="flex-1 border-t border-dashed border-current opacity-40" />
    </div>
  );
}

/** Where the gold window starts or ends between two messages. */
export function WindowEdge({ entering, window, id }) {
  return (
    <Divider tone="text-yellow-800" id={id}>
      <span className="font-semibold">
        {entering ? `Window starts (${formatDateTime(window?.start)})` : `Window ends (${formatDateTime(window?.end)})`}
      </span>
    </Divider>
  );
}

/** `text` with every case-insensitive occurrence of `query` marked. */
function Highlighted({ text, query }) {
  const needle = query?.trim().toLocaleLowerCase();
  if (!needle) return text;
  const lower = text.toLocaleLowerCase();
  const parts = [];
  let from = 0;
  for (let at = lower.indexOf(needle); at !== -1; at = lower.indexOf(needle, from)) {
    parts.push(text.slice(from, at), <mark key={at} className="rounded bg-yellow-200 text-slate-900">{text.slice(at, at + needle.length)}</mark>);
    from = at + needle.length;
  }
  parts.push(text.slice(from));
  return parts;
}

/**
 * Who this message replies to: the quoted person and time, and, when the quoted message sits
 * in another conversation, that conversation's chip — a strong hint that one of the two is
 * in the wrong place. A click scrolls to the quoted message (or opens its conversation).
 */
function ReplyLink({ reply, own, colors, onJump }) {
  const other = reply.thread_id !== own;
  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        onJump(reply);
      }}
      className={`inline-flex items-center gap-1 hover:underline ${other ? "text-amber-800" : "text-blue-700"}`}
      title={other ? `The quoted message is in conversation ${reply.thread_id}` : "Show the quoted message"}
    >
      ↩ replies to <Bidi>{reply.sender}</Bidi> {formatTime(reply.datetime)}
      {other && (
        <>
          {" "}in <ThreadTag info={colors.get(reply.thread_id)} threadId={reply.thread_id} /> <Ltr>{reply.thread_id}</Ltr>
        </>
      )}
    </button>
  );
}

/**
 * One message. `info`: its conversation's colour; `selected` / `onToggle` (omit `onToggle`
 * for a read-only view); `outside`: dimmed (outside the gold window); `showTag`: the
 * conversation chip before the time (timeline); `highlight`: a search to mark.
 */
export function MessageRow({ message, info, colors, selected, onToggle, outside, showTag, highlight, onJump }) {
  const selectable = Boolean(onToggle);
  const activate = (event) => {
    if (!selectable || event.target.closest("a, button")) return;
    onToggle(event.shiftKey);
  };
  return (
    <div
      id={`msg-${message.id}`}
      role={selectable ? "button" : undefined}
      tabIndex={selectable ? 0 : undefined}
      aria-pressed={selectable ? selected : undefined}
      onClick={activate}
      onKeyDown={(event) => {
        if (selectable && (event.key === " " || event.key === "Enter") && event.target === event.currentTarget) {
          event.preventDefault();
          onToggle(event.shiftKey);
        }
      }}
      className={`flex gap-2 rounded-md border border-s-4 px-2 py-1.5 ${info?.bar ?? ""} ${
        selected ? "border-blue-500 bg-blue-50 ring-1 ring-blue-500" : "border-slate-200 bg-white"
      } ${outside ? "opacity-60" : ""} ${selectable ? "cursor-pointer select-none" : ""}`}
    >
      {selectable && <input type="checkbox" checked={selected} readOnly tabIndex={-1} aria-hidden="true" className="mt-1 shrink-0 pointer-events-none" />}
      <div className="min-w-0 flex-1">
        <div className="mb-0.5 flex flex-wrap items-center gap-x-2 text-xs text-slate-500">
          {showTag && <ThreadTag info={info} threadId={message.thread_id} />}
          <span>{formatTime(message.datetime)}</span>
          <Bidi className="font-medium text-slate-700">{message.sender}</Bidi>
          {message.reply_to && <ReplyLink reply={message.reply_to} own={message.thread_id} colors={colors} onJump={onJump} />}
        </div>
        {message.quote && (
          <Bidi as="div" className="mb-1 line-clamp-3 border-s-2 border-slate-300 ps-2 text-xs text-slate-500">
            {message.quote.text}
          </Bidi>
        )}
        <Bidi as="p" text={message.content} className="whitespace-pre-wrap break-words text-sm text-slate-900">
          <Highlighted text={message.content || ""} query={highlight} />
        </Bidi>
        {message.reactions?.length > 0 && (
          <div className="mt-0.5 text-xs text-slate-500">{message.reactions.map((reaction) => reaction.emoji).join(" ")}</div>
        )}
      </div>
    </div>
  );
}

/** Scroll to a shown message (briefly ringed), or report that it is not on the page. */
export function scrollToMessage(id) {
  const element = document.getElementById(`msg-${id}`);
  if (!element) return false;
  element.scrollIntoView({ behavior: "smooth", block: "center" });
  element.classList.add("ring-2", "ring-amber-500");
  setTimeout(() => element.classList.remove("ring-2", "ring-amber-500"), 1500);
  return true;
}
