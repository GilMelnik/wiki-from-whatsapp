import { Bidi, countText, formatDateTime, Ltr, MessageRow, Panel, PanelState, ToolLink } from "@shell";
import { firstTagIn, STATUS_LABELS, statsText, tagIn, tagSpan, tagText, THREADS_TOOL } from "./tagged.js";

// Main column: who the tagged member is to the chat (their tags, how often and by how many
// members they are tagged, how much they write), the names step 3's claims already carry in
// the conversations where they are tagged (a click puts one in the name field; nothing is
// applied by itself), every tagging message with its date, time and sender, the tag marked,
// and the conversations that hold them. A link opens the conversation in the Conversations
// page, in a new tab, at the tagging message (ringed there for a few seconds). Only a message
// whose own text holds the tag is listed: a reply to the member, or to a tagging message, is not.

function Section({ title, children }) {
  return (
    <section className="space-y-2">
      <h3 className="section-title">{title}</h3>
      {children}
    </section>
  );
}

function ConversationLink({ threadId, messageId, children }) {
  const params = messageId ? { thread: threadId, message: messageId } : { thread: threadId };
  return (
    <ToolLink toolId={THREADS_TOOL} params={params} newTab className="text-blue-700 hover:underline" title="Opens the conversation in the Conversations page, in a new tab, at the tagging message">
      {children}
    </ToolLink>
  );
}

function Suggestions({ suggestions, onPick }) {
  if (suggestions.length === 0) {
    return <p className="text-xs text-slate-500">Step 3's claims name nothing in these conversations yet.</p>;
  }
  return (
    <>
      <div className="flex max-h-48 flex-wrap gap-1.5 overflow-y-auto">
        {suggestions.map((suggestion) => (
          <button
            key={suggestion.name}
            type="button"
            className={`btn btn-sm ${suggestion.near > 0 ? "border-emerald-400" : ""}`}
            title={`${countText(suggestion.claims, "claim")} in these conversations, ${suggestion.near} of them from a tagging message. Click to put the name in the field.`}
            onClick={() => onPick(suggestion.name)}
          >
            <Bidi>{suggestion.name}</Bidi>
            <span className="text-xs text-slate-500">×{suggestion.claims}</span>
          </button>
        ))}
      </div>
      <p className="text-xs text-slate-500">
        Names first that claims drawn from a tagging message carry (green edge), then the most frequent. A click only fills the name field.
      </p>
    </>
  );
}

function Conversations({ threads, messages }) {
  if (threads.length === 0) return <p className="text-xs text-slate-500">No conversation holds these messages (step 1 has not placed them).</p>;
  return (
    <ul className="space-y-1">
      {threads.map((thread) => (
        <li key={thread.thread_id} className="flex flex-wrap items-center gap-x-3">
          <ConversationLink threadId={thread.thread_id} messageId={firstTagIn(messages, thread.thread_id)}>
            <Ltr>{thread.thread_id}</Ltr>
          </ConversationLink>
          <span className="text-xs text-slate-500">
            started {formatDateTime(thread.start_time)} · {countText(thread.tag_count, "tag")}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Messages({ member }) {
  return (
    <div className="space-y-1.5">
      {member.messages.map((message) => (
        <div key={message.id} className="space-y-0.5">
          <MessageRow message={message} highlight={`@${tagIn(message.content, member.tags)}`} />
          {message.thread_id && (
            <p className="ps-3 text-xs">
              <ConversationLink threadId={message.thread_id} messageId={message.id}>Open its conversation at this message</ConversationLink>
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

function Subtitle({ member }) {
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <span>{statsText(member)}</span>
      <span>tagged {tagSpan(member)}</span>
      <span title="The member id">
        <Ltr>{member.member}</Ltr>
      </span>
    </span>
  );
}

export function MemberDetail({ detail, selectedId, onPick }) {
  if (!selectedId) return <PanelState empty emptyText="Pick a member in the list" />;
  const member = detail.data;
  return (
    <PanelState loading={detail.loading} error={detail.error} hasData={Boolean(member)} onRetry={detail.reload}>
      {member && (
        <Panel title={`${tagText(member)} · ${STATUS_LABELS[member.status]}`} subtitle={<Subtitle member={member} />}>
          <div className="max-w-4xl space-y-6 p-4 text-sm">
            {member.tags.length > 1 && (
              <p className="text-xs text-slate-500">
                The chat writes this member's tag in {member.tags.length} ways: {member.tags.map((tag) => `@${tag}`).join(", ")}.
              </p>
            )}
            <Section title="Suggested names">
              <Suggestions suggestions={member.suggestions} onPick={onPick} />
            </Section>
            <Section title={`The tagging messages (${member.messages.length})`}>
              <p className="text-xs text-slate-500">
                Only messages whose own text holds the tag (marked in yellow). The grey line above a message is the message it replies to; a reply by itself is never counted as a tag.
              </p>
              <Messages member={member} />
            </Section>
            <Section title={`Tagged in ${countText(member.threads.length, "conversation")}`}>
              <Conversations threads={member.threads} messages={member.messages} />
            </Section>
          </div>
        </Panel>
      )}
    </PanelState>
  );
}
