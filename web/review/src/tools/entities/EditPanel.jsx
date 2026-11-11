import { useEffect, useState } from "react";
import { Bidi, countText, Kbd, Ltr, Panel, PanelState } from "@shell";
import { Contacts } from "./Contacts.jsx";
import { KINDS, kindNote } from "./entities.js";

// Side column: what can be done with the selected entity. Decisions in the footer (accept,
// merge, reject, delete); the name, contacts and claim grouping above. Every change is saved
// at once to entities_edited.json (claim groups to claim_aggregations.json).

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
      <input ref={nameRef} dir="rtl" lang="he" className="field w-full" value={name} onChange={(event) => setName(event.target.value)} />
      <button type="submit" className="btn btn-sm" disabled={saving || !dirty}>
        Save name
      </button>
    </form>
  );
}

function KindControl({ entity, saving, onSetKind }) {
  return (
    <section className="space-y-1">
      <h3 className="section-title">
        <label htmlFor="entity-kind">Kind</label>
        {entity.kind_manual && <span className="badge badge-sky ms-1">Set by hand</span>}
      </h3>
      <select
        id="entity-kind"
        className="field w-full"
        value={entity.kind ?? ""}
        disabled={saving}
        onChange={(event) => onSetKind(event.target.value)}
      >
        {!entity.kind && (
          <option value="" disabled>
            Not classified yet
          </option>
        )}
        {KINDS.map((kind) => (
          <option key={kind.value} value={kind.value}>
            {kind.label}
          </option>
        ))}
      </select>
      <p className="text-xs text-slate-500">
        {kindNote(entity)} A negative claim about a person reaches the wiki only when two members reported it.
      </p>
    </section>
  );
}

function Footer({ entity, saving, pickedCount, on }) {
  const accepted = entity.status === "accepted";
  return (
    <>
      {accepted ? (
        <button type="button" className="btn btn-block" disabled={saving} onClick={() => on.setStatus("suggested")}>
          Back to suggested (for another look)
        </button>
      ) : (
        <button type="button" className="btn btn-primary btn-block" disabled={saving} onClick={() => on.setStatus("accepted")}>
          Accept the entity
          <Kbd>a</Kbd>
        </button>
      )}
      <div className="grid grid-cols-2 gap-2">
        <button type="button" className="btn btn-sm" disabled={saving} onClick={on.merge}>
          Merge into another entity…
          <Kbd>m</Kbd>
        </button>
        <button type="button" className="btn btn-sm" disabled={saving} onClick={on.reject}>
          Reject the grouping…
          <Kbd>x</Kbd>
        </button>
        <button type="button" className="btn btn-sm" disabled={saving || pickedCount < 2} onClick={on.aggregate}>
          Combine {countText(pickedCount, "claim")}…
          <Kbd>g</Kbd>
        </button>
        <button type="button" className="btn btn-sm text-rose-700" disabled={saving} onClick={on.remove}>
          Delete the entity…
          <Kbd>Del</Kbd>
        </button>
      </div>
    </>
  );
}

export function EditPanel({ detail, saving, nameRef, pickedCount, on }) {
  const entity = detail.data?.entity;
  return (
    <Panel
      title="Edit"
      subtitle={
        <>
          Saved at once to <Ltr>entities_edited.json</Ltr>
        </>
      }
      footer={entity && <Footer entity={entity} saving={saving} pickedCount={pickedCount} on={on} />}
    >
      <PanelState empty={!entity} emptyText="No entity selected">
        {entity && (
          <div className="space-y-6 p-4 text-sm">
            <NameForm entity={entity} saving={saving} nameRef={nameRef} onRename={on.rename} />
            <KindControl entity={entity} saving={saving} onSetKind={on.setKind} />
            <Contacts entity={entity} saving={saving} onSave={on.saveContacts} onResolve={on.resolveContact} />
            <section>
              <h3 className="section-title">All names</h3>
              <p className="flex flex-wrap gap-1">
                {entity.aliases.map((alias) => (
                  <Bidi key={alias} className="badge badge-slate">
                    {alias}
                  </Bidi>
                ))}
              </p>
            </section>
            <section className="space-y-1 text-xs text-slate-500">
              <p>Pick claims (the check box) to copy, move, mark as unrelated or combine them.</p>
              <p>“Move name” moves one whole alias; “Merge” moves every name to another entity.</p>
              <p>“Reject the grouping” splits the entity into one entity per name, each with only the claims tagged with that name.</p>
            </section>
          </div>
        )}
      </PanelState>
    </Panel>
  );
}
