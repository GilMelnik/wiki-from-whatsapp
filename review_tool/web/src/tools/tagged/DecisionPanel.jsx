import { Bidi, formatDateTime, Kbd, Panel, PanelState } from "@shell";
import { cleanName, STATUS_LABELS } from "./tagged.js";

// The side column: the decision on the selected member and what step 3 will show for their tag.
// A provider is saved with the public name typed (or picked from the suggestions); "not a
// provider" and an undecided member are shown to step 3 as an anonymous member. Saved at once.

export const NAME_FIELD_ID = "tagged-public-name";

function Shown({ member, aliasExample }) {
  const name = member.status === "provider" ? member.public_name : null;
  return (
    <p className="text-xs text-slate-600">
      Step 3 shows this tag as{" "}
      <Bidi className="font-medium text-slate-900">{name ? `@${name}` : `@${aliasExample ?? "…"}`}</Bidi>
      {name ? ": a named provider." : ": an anonymous member, never an entity."}
    </p>
  );
}

export function DecisionPanel({ detail, aliasExample, name, onName, saving, onProvider, onNotProvider, onClear }) {
  const member = detail.data;
  const typed = cleanName(name);
  const unchanged = member?.status === "provider" && member.public_name === typed;
  return (
    <Panel title="Decide">
      <PanelState empty={!member} emptyText="No member selected">
        {member && (
          <div className="space-y-4 p-4 text-sm">
            <p>
              Now: <span className="font-medium">{STATUS_LABELS[member.status]}</span>
              {member.decided_at && <span className="text-xs text-slate-500"> (decided {formatDateTime(member.decided_at)})</span>}
            </p>
            <Shown member={member} aliasExample={aliasExample} />
            <form
              className="space-y-2"
              onSubmit={(event) => {
                event.preventDefault();
                if (typed && !unchanged) onProvider(typed);
              }}
            >
              <label htmlFor={NAME_FIELD_ID} className="block text-xs text-slate-600">
                Public name (as the wiki should name them) <Kbd>p</Kbd>
              </label>
              <input
                id={NAME_FIELD_ID}
                type="text"
                dir="auto"
                lang="he"
                className="field w-full"
                value={name}
                disabled={saving}
                onChange={(event) => onName(event.target.value)}
              />
              <button type="submit" className="btn btn-block btn-primary justify-between" disabled={saving || !typed || unchanged}>
                <span>{member.status === "provider" ? "Save the new name" : "A provider, under this name"}</span>
                <Kbd>Enter</Kbd>
              </button>
            </form>
            <button
              type="button"
              className="btn btn-block justify-between"
              disabled={saving || member.status === "not_provider"}
              onClick={onNotProvider}
            >
              <span>Not a provider</span>
              <Kbd>n</Kbd>
            </button>
            <button type="button" className="btn btn-block justify-between" disabled={saving || member.status === "undecided"} onClick={onClear}>
              <span>Clear the decision</span>
              <Kbd>c</Kbd>
            </button>
            <p className="text-xs text-slate-500">
              Decide before step 3 runs. A change makes step 3 read again the conversations where this tag is shown differently.
            </p>
          </div>
        )}
      </PanelState>
    </Panel>
  );
}
