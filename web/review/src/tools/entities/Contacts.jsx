import { useEffect, useState } from "react";
import { Bidi, Ltr } from "@shell";
import { CONTACT_KINDS, contactRows, contactsChanged, contactsToForm, formToContacts } from "./entities.js";

// Contacts, two ways. In the main column, for the decision "which contacts are right": every
// contact with the claim it was found in, and what can be done with it: an uncertain one
// (seen only in claims naming several entities) attached, with a correction if needed, or
// rejected; a rejected one (step 4 never brings it back) restored. In the side column, the
// confident contacts edited by hand (saving makes them manual, so step 4 keeps them).

const KIND_LABELS = Object.fromEntries(CONTACT_KINDS.map(({ key, label }) => [key, label]));
const STATE_LABELS = { contact: "Contact", uncertain: "Uncertain", rejected: "Rejected" };
const STATE_TONES = { contact: "badge-emerald", uncertain: "badge-amber", rejected: "badge-slate" };

function Source({ source }) {
  if (!source) return <p className="text-xs text-slate-500">Not found in its claims (added by hand).</p>;
  return (
    <p className="text-xs text-slate-600" title={`Claim ${source.claim_id}`}>
      From a claim about “<Bidi>{source.name}</Bidi>”:{" "}
      <Bidi className="text-slate-800">{source.claim_text}</Bidi>
    </p>
  );
}

function UncertainActions({ row, saving, onResolve }) {
  const [draft, setDraft] = useState(row.value);
  useEffect(() => setDraft(row.value), [row.value]);
  const corrected = draft.trim() !== row.value ? draft.trim() : null;
  return (
    <span className="flex flex-wrap items-center gap-1">
      <input dir="ltr" aria-label="The value, corrected if needed" className="field min-w-0 flex-1 font-mono text-xs" value={draft} onChange={(event) => setDraft(event.target.value)} />
      <button
        type="button"
        className="btn btn-sm"
        disabled={saving || !draft.trim()}
        onClick={() => onResolve({ kind: row.kind, value: row.value, action: "accept", new_value: corrected })}
      >
        {corrected ? "Attach corrected" : "Attach"}
      </button>
      <button type="button" className="btn btn-sm text-rose-700" disabled={saving} onClick={() => onResolve({ kind: row.kind, value: row.value, action: "reject" })}>
        Reject
      </button>
    </span>
  );
}

export function ContactsReview({ entity, saving, onResolve }) {
  const rows = contactRows(entity);
  if (rows.length === 0) return null;
  return (
    <section className="space-y-2">
      <h3 className="section-title">Contacts {entity.contacts_manual && <span className="badge badge-sky ms-1">Edited by hand</span>}</h3>
      <ul className="space-y-2">
        {rows.map((row) => (
          <li key={`${row.state}-${row.kind}-${row.value}`} className="card space-y-1.5">
            <p className="flex flex-wrap items-center gap-2">
              <span className={`badge ${STATE_TONES[row.state]}`}>{STATE_LABELS[row.state]}</span>
              <span className="text-xs text-slate-500">{KIND_LABELS[row.kind]}</span>
              <Ltr className={`break-all ${row.state === "rejected" ? "text-slate-500 line-through" : "text-slate-900"}`}>{row.value}</Ltr>
              {row.state === "rejected" && (
                <button type="button" className="btn btn-sm ms-auto" disabled={saving} onClick={() => onResolve({ kind: row.kind, value: row.value, action: "restore" })}>
                  Undo the rejection
                </button>
              )}
            </p>
            <Source source={row.source} />
            {row.state === "uncertain" && (
              <>
                <p className="text-xs text-amber-800">Seen only in claims that name several entities: is it this one's?</p>
                <UncertainActions row={row} saving={saving} onResolve={onResolve} />
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function ContactsForm({ entity, saving, onSave }) {
  const [form, setForm] = useState(() => contactsToForm(entity.contacts));
  useEffect(() => setForm(contactsToForm(entity.contacts)), [entity.entity_id, entity.contacts]);
  const dirty = contactsChanged(entity.contacts, form);
  return (
    <details className="space-y-2" open={dirty || undefined}>
      <summary className="cursor-pointer section-title">Edit the contacts by hand</summary>
      {CONTACT_KINDS.map(({ key, label }) => (
        <label key={key} className="block">
          <span className="text-xs text-slate-500">{label} (one value per line)</span>
          <textarea
            dir="ltr"
            rows={2}
            className="field w-full font-mono text-xs"
            value={form[key]}
            onChange={(event) => setForm((previous) => ({ ...previous, [key]: event.target.value }))}
          />
        </label>
      ))}
      <button type="button" className="btn btn-sm" disabled={saving || !dirty} onClick={() => onSave(formToContacts(form))}>
        Save contacts
      </button>
    </details>
  );
}
