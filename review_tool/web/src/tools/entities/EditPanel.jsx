import { useEffect, useState } from "react";
import { countText, Kbd, Panel, PanelState } from "@shell";
import { ContactsForm } from "./Contacts.jsx";

// The side column: the decisions on the selected entity, only those that fit it, each with
// its key (accept, or back to suggested; merge into another entity; split a grouping of
// several names; combine picked claims; delete), then its canonical name and the contacts
// edited by hand. Every change is saved at once.

function ActionButton({ label, keyName, primary = false, danger = false, disabled, onClick }) {
  const tone = primary ? "btn-primary" : danger ? "text-rose-700" : "";
  return (
    <button type="button" className={`btn btn-block justify-between ${tone}`} disabled={disabled} onClick={onClick}>
      <span>{label}</span>
      {keyName && <Kbd>{keyName}</Kbd>}
    </button>
  );
}

function NameForm({ entity, saving, nameRef, onRename }) {
  const [name, setName] = useState(entity.canonical_name);
  useEffect(() => setName(entity.canonical_name), [entity.entity_id, entity.canonical_name]);
  const dirty = name.trim() !== entity.canonical_name && name.trim() !== "";
  return (
    <form
      className="space-y-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (dirty && !saving) onRename(name.trim());
      }}
    >
      <h3 className="section-title">
        Canonical name {entity.canonical_manual && <span className="badge badge-sky ms-1">Set by hand</span>}
      </h3>
      <div className="flex gap-2">
        <input ref={nameRef} dir="rtl" lang="he" className="field min-w-0 flex-1" value={name} onChange={(event) => setName(event.target.value)} />
        <button type="submit" className="btn btn-sm" disabled={saving || !dirty}>
          Save
        </button>
      </div>
      <p className="text-xs text-slate-500">
        Or “Make canonical” on one of its names. <Kbd>e</Kbd> edits it.
      </p>
    </form>
  );
}

function Decisions({ entity, saving, pickedCount, on }) {
  const accepted = entity.status === "accepted";
  return (
    <div className="space-y-2">
      {accepted ? (
        <ActionButton label="Back to suggested" disabled={saving} onClick={() => on.setStatus("suggested")} />
      ) : (
        <ActionButton label="Accept the entity" keyName="a" primary disabled={saving} onClick={() => on.setStatus("accepted")} />
      )}
      <ActionButton label="Merge into another entity…" keyName="m" disabled={saving} onClick={on.merge} />
      {entity.member_count > 1 && (
        <ActionButton label="Split into one entity per name…" keyName="x" disabled={saving} onClick={on.reject} />
      )}
      {pickedCount >= 2 && (
        <ActionButton label={`Combine the ${countText(pickedCount, "picked claim")}…`} keyName="g" disabled={saving} onClick={on.aggregate} />
      )}
      <ActionButton label="Delete the entity…" keyName="Del" danger disabled={saving} onClick={on.remove} />
    </div>
  );
}

export function EditPanel({ detail, saving, nameRef, pickedCount, on }) {
  const entity = detail.data?.entity;
  return (
    <Panel title="Decide">
      <PanelState empty={!entity} emptyText="No entity selected">
        {entity && (
          <div className="space-y-6 p-4 text-sm">
            <Decisions entity={entity} saving={saving} pickedCount={pickedCount} on={on} />
            <NameForm entity={entity} saving={saving} nameRef={nameRef} onRename={on.rename} />
            <ContactsForm entity={entity} saving={saving} onSave={on.saveContacts} />
            <p className="text-xs text-slate-500">
              Pick claims (their check box) to copy, move, mark as unrelated or combine them. “Move name” moves one name with its
              claims to another entity.
            </p>
          </div>
        )}
      </PanelState>
    </Panel>
  );
}
