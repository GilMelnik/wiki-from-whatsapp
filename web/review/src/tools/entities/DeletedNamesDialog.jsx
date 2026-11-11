import { Bidi, countText, Dialog, PanelState, useResource } from "@shell";
import { api } from "./api.js";

// Names of deleted entities: step 4 never brings them back into any entity. Restoring one
// makes it a new suggested entity with every claim that names it.

export function DeletedNamesDialog({ reads, saving, onClose, onRestore }) {
  const names = useResource(() => api.get("/deleted-names"), [], { watch: reads });
  const list = names.data?.names ?? [];
  return (
    <Dialog open size="lg" title="Deleted names" onClose={onClose}>
      <p className="mb-3 text-slate-600">
        Names of deleted entities. Step 4 will not bring them back into any entity, even when new claims
        mention them.
      </p>
      <PanelState
        loading={names.loading}
        error={names.error}
        hasData={Boolean(names.data)}
        onRetry={names.reload}
        empty={names.data && list.length === 0}
        emptyText="No names were deleted"
      >
        <ul className="divide-y divide-slate-100">
          {list.map((entry) => (
            <li key={entry.name} className="flex items-center gap-2 py-2">
              <Bidi className="flex-1 font-medium">{entry.name}</Bidi>
              <span className="text-xs text-slate-500">{countText(entry.claim_count, "claim")}</span>
              <button
                type="button"
                className="btn btn-sm"
                disabled={saving || entry.claim_count === 0}
                title={entry.claim_count === 0 ? "No claim mentions the name any more" : undefined}
                onClick={() => onRestore(entry.name)}
              >
                Restore as entity
              </button>
            </li>
          ))}
        </ul>
      </PanelState>
    </Dialog>
  );
}
