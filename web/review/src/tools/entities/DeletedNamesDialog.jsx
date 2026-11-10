import { Bidi, Dialog, formatCount, PanelState, useResource } from "@shell";
import { api } from "./api.js";

// Names of deleted entities: step 4 never brings them back into any entity. Restoring one
// makes it a new suggested entity with every claim that names it.

export function DeletedNamesDialog({ reads, saving, onClose, onRestore }) {
  const names = useResource(() => api.get("/deleted-names"), [], { watch: reads });
  const list = names.data?.names ?? [];
  return (
    <Dialog open size="lg" title="שמות שנמחקו" onClose={onClose}>
      <p className="mb-3 text-slate-600">
        שמות של ישויות שנמחקו. שלב 4 לא יחזיר אותם לאף ישות, גם כשטענות חדשות מזכירות אותם.
      </p>
      <PanelState
        loading={names.loading}
        error={names.error}
        hasData={Boolean(names.data)}
        onRetry={names.reload}
        empty={names.data && list.length === 0}
        emptyText="לא נמחקו שמות"
      >
        <ul className="divide-y divide-slate-100">
          {list.map((entry) => (
            <li key={entry.name} className="flex items-center gap-2 py-2">
              <Bidi className="flex-1 font-medium">{entry.name}</Bidi>
              <span className="text-xs text-slate-500">{formatCount(entry.claim_count)} טענות</span>
              <button
                type="button"
                className="btn btn-sm"
                disabled={saving || entry.claim_count === 0}
                title={entry.claim_count === 0 ? "אף טענה כבר לא מזכירה את השם" : undefined}
                onClick={() => onRestore(entry.name)}
              >
                שחזור כישות
              </button>
            </li>
          ))}
        </ul>
      </PanelState>
    </Dialog>
  );
}
