import { ActionButton, Kbd, Panel } from "@shell";

// Side column of the merge suggestions: the decision on the pair, one key each. Merge and "not
// the same" are saved at once and the next pair comes up; skip only moves on; "u" forgets the
// last "not the same". A pair whose groups dispute each other asks before merging.

export function DecisionPanel({ pair, saving, canUndo, onMerge, onNotSame, onSkip, onUndo }) {
  return (
    <Panel title="Decide">
      <div className="space-y-4 p-4 text-sm">
        {pair ? (
          <div className="space-y-2">
            <ActionButton label={pair.dispute ? "The same – merge…" : "The same – merge"} keyName="m" primary disabled={saving} onClick={onMerge} />
            <ActionButton label="Not the same" keyName="n" disabled={saving} onClick={onNotSame} />
            <ActionButton label="Skip – decide later" keyName="s" disabled={saving} onClick={onSkip} />
            {canUndo && <ActionButton label="Undo the last “not the same”" keyName="u" disabled={saving} onClick={onUndo} />}
          </div>
        ) : (
          <p className="text-slate-500">
            Pick a suggestion in the list (<Kbd>j</Kbd> / <Kbd>k</Kbd>).
          </p>
        )}
        <div className="space-y-2 text-xs text-slate-500">
          <p>A merge adds group B to group A and counts the people again, as step 5 would.</p>
          <p>“Not the same” is remembered for every pair of their claims, so the two are not suggested again, even after merges, splits and new runs of step 5.</p>
        </div>
      </div>
    </Panel>
  );
}
