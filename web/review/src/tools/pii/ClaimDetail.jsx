import { Bidi, countText, formatDateTime, Kbd, Ltr, MessageRow, Panel, PanelState } from "@shell";
import { ClaimText } from "./ClaimText.jsx";
import { dateRange, marksToRestore, messagesHolding, otherMessages, OWNER_LABELS, STATUS_LABELS, typeLabel } from "./review.js";

// Main column: the claim with its removed details in place, then each removed detail with what
// the decision rests on: whose contact the value is (step 4's entities) and the chat message it
// came from (every message shown with its date, time and sender); then the text a restore
// would save, and folded away the claim's other messages.

function Section({ title, children }) {
  return (
    <section className="space-y-2">
      <h3 className="section-title">{title}</h3>
      {children}
    </section>
  );
}

function Owners({ owners, entitiesKnown }) {
  if (!entitiesKnown) return <p className="text-xs text-slate-500">The entities file is not there yet (step 4), so contacts cannot be checked.</p>;
  if (owners.length === 0) return <p className="text-xs text-slate-600">Not a contact of any known entity.</p>;
  return (
    <ul className="space-y-0.5 text-xs">
      {owners.map((owner) => (
        <li key={`${owner.entity_id}-${owner.how}`} className={owner.how === "rejected" ? "text-slate-600" : "text-emerald-800"}>
          Listed as {OWNER_LABELS[owner.how]} “<Bidi className="font-medium">{owner.canonical_name}</Bidi>” <Ltr className="text-slate-400">{owner.entity_id}</Ltr>
        </li>
      ))}
    </ul>
  );
}

function Sources({ messages, index, value, chatKnown }) {
  if (!chatKnown) return <p className="text-xs text-slate-500">The chat export is not there, so the messages cannot be shown.</p>;
  const holding = messagesHolding(messages, index);
  if (holding.length === 0) {
    return <p className="text-xs text-slate-500">None of the claim's messages has this value word for word; its other messages are below.</p>;
  }
  return (
    <div className="space-y-1.5">
      {holding.map((message) => (
        <MessageRow key={message.id} message={message} highlight={value} />
      ))}
    </div>
  );
}

function RemovedDetail({ claim, redaction, index, pending }) {
  return (
    <li className="card space-y-2">
      <p className="flex flex-wrap items-center gap-2">
        {pending && claim.redactions.length > 1 && <Kbd>{index + 1}</Kbd>}
        <span className="text-xs text-slate-500">{typeLabel(redaction.type)}</span>
        <Ltr className="break-all font-medium text-slate-900">{redaction.value}</Ltr>
        {!pending && (
          <span className={`badge ${redaction.in_text ? "badge-emerald" : "badge-slate"}`}>{redaction.in_text ? "Restored" : "Removed"}</span>
        )}
      </p>
      <Owners owners={redaction.owners ?? []} entitiesKnown={claim.context?.entities} />
      <Sources messages={claim.messages} index={index} value={redaction.value} chatKnown={claim.context?.chat} />
    </li>
  );
}

function OtherMessages({ messages }) {
  const others = otherMessages(messages);
  if (others.length === 0) return null;
  return (
    <details>
      <summary className="cursor-pointer text-xs text-slate-600">The claim's other messages ({countText(others.length, "message")})</summary>
      <div className="mt-2 space-y-1.5">
        {others.map((message) => (
          <MessageRow key={message.id} message={message} />
        ))}
      </div>
    </details>
  );
}

function TheClaim({ claim }) {
  const pending = claim.status === "pending";
  if (pending) {
    return (
      <>
        <Section title="The claim, as step 3 left it">
          <ClaimText segments={claim.segments} redactions={claim.redactions} mode="scrubbed" />
        </Section>
        {!claim.marks_match && (
          <p className="note note-warning">
            The number of marks in the text does not match the number of removed details, so the details cannot be put back
            automatically. You can keep the removal.
          </p>
        )}
      </>
    );
  }
  return (
    <Section title="The claim, as saved">
      <Bidi as="p" className="leading-relaxed">
        {claim.text}
      </Bidi>
      {claim.reviewed_at && <p className="text-xs text-slate-500">Decided {formatDateTime(claim.reviewed_at)}.</p>}
      {!claim.can_undo && (
        <p className="text-xs text-slate-500">The original file no longer has the removed details, so this cannot be undone.</p>
      )}
    </Section>
  );
}

function Restored({ claim, chosen }) {
  if (claim.status !== "pending" || !claim.marks_match) return null;
  const restored = marksToRestore(chosen, claim.redactions.length);
  return (
    <Section title={chosen.length > 0 ? "The claim if you restore the marked details" : "The claim if you restore"}>
      <div className="card">
        <ClaimText segments={claim.segments} redactions={claim.redactions} restored={restored} mode="preview" />
      </div>
    </Section>
  );
}

function Subtitle({ claim }) {
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1" title={`Claim ${claim.claim_id}`}>
      <span>
        Said <Ltr>{dateRange(claim.first_date, claim.last_date)}</Ltr>
      </span>
      {claim.entities.length > 0 && (
        <span>
          Names{" "}
          {claim.entities.map((name, index) => (
            <span key={name}>
              {index > 0 && ", "}“<Bidi>{name}</Bidi>”
            </span>
          ))}
        </span>
      )}
    </span>
  );
}

export function ClaimDetail({ detail, selectedId, chosen }) {
  if (!selectedId) return <PanelState empty emptyText="Pick a claim in the list" />;
  const claim = detail.data?.claim;
  return (
    <PanelState loading={detail.loading} error={detail.error} hasData={Boolean(claim)} onRetry={detail.reload}>
      {claim && (
        <Panel title={STATUS_LABELS[claim.status]} subtitle={<Subtitle claim={claim} />}>
          <div className="max-w-4xl space-y-6 p-4 text-sm">
            <TheClaim claim={claim} />
            <Section title={claim.redactions.length === 1 ? "The removed detail" : "The removed details"}>
              {claim.redactions.length === 0 ? (
                <p className="text-slate-500">The original file no longer says what was removed from the claim.</p>
              ) : (
                <ul className="space-y-3">
                  {claim.redactions.map((redaction, index) => (
                    <RemovedDetail key={index} claim={claim} redaction={redaction} index={index} pending={claim.status === "pending"} />
                  ))}
                </ul>
              )}
            </Section>
            <Restored claim={claim} chosen={chosen} />
            <OtherMessages messages={claim.messages} />
          </div>
        </Panel>
      )}
    </PanelState>
  );
}
