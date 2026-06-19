const STANCE_LABELS = {
  positive: "חיובי",
  negative: "שלילי",
  neutral: "ניטרלי",
  factual: "עובדתי",
};

export default function GroupDetailPanel({
  group,
  queue,
  selectedMembers,
  onToggleMember,
  onSetRepresentative,
  onMoveMember,
  onSplit,
  onPrev,
  onNext,
  saving,
}) {
  if (!group) {
    return (
      <div className="flex-1 flex items-center justify-center text-slate-500 text-sm">
        בחר קבוצה מהרשימה
      </div>
    );
  }

  const canSplit =
    selectedMembers.size > 0 &&
    selectedMembers.size < (group.members?.length || 0);

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-3 border-b border-slate-200 bg-white shrink-0">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">טענה מייצגת</h2>
          {queue && (
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <button
                type="button"
                disabled={!queue.prev_key}
                onClick={() => onPrev(queue.prev_key)}
                className="px-2 py-1 border rounded disabled:opacity-30"
              >
                הקודם
              </button>
              <span>
                {queue.position}/{queue.total}
              </span>
              <button
                type="button"
                disabled={!queue.next_key}
                onClick={() => onNext(queue.next_key)}
                className="px-2 py-1 border rounded disabled:opacity-30"
              >
                הבא
              </button>
            </div>
          )}
        </div>
        <p className="mt-2 text-sm leading-relaxed bg-teal-50 border border-teal-200 rounded p-3">
          {group.claim_text}
        </p>
        <div className="mt-2 flex flex-wrap gap-2 text-xs text-slate-500">
          {group.stance && (
            <span>{STANCE_LABELS[group.stance] || group.stance}</span>
          )}
          <span>תמיכה: {group.support_count ?? "—"}</span>
          <span>{group.endorsement_count} טענות בקבוצה</span>
          <span>{group.thread_count} שיחות</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold">חברי הקבוצה</h3>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={!canSplit || saving}
              onClick={onSplit}
              className="text-xs px-2 py-1 rounded border border-slate-300 hover:bg-amber-50 disabled:opacity-40"
            >
              פיצול ({selectedMembers.size})
            </button>
          </div>
        </div>
        <div className="space-y-2">
          {(group.members || []).map((member) => {
            const checked = selectedMembers.has(member.source_claim_id);
            return (
              <article
                key={member.source_claim_id}
                className={`border rounded-lg p-3 bg-white ${
                  member.is_representative ? "member-representative" : "border-slate-200"
                }`}
              >
                <div className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => onToggleMember(member.source_claim_id)}
                    className="mt-1 shrink-0"
                  />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm leading-relaxed">{member.claim_text}</p>
                    <div className="mt-2 flex flex-wrap gap-2 text-xs text-slate-500">
                      <code>{member.source_claim_id}</code>
                      {member.thread_id && <span>שיחה: {member.thread_id}</span>}
                      {member.stance && (
                        <span>{STANCE_LABELS[member.stance] || member.stance}</span>
                      )}
                      {member.is_representative && (
                        <span className="text-teal-700 font-medium">מייצג</span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="mt-2 flex gap-2 justify-end">
                  {!member.is_representative && (
                    <button
                      type="button"
                      disabled={saving}
                      onClick={() => onSetRepresentative(member.source_claim_id)}
                      className="text-xs px-2 py-1 rounded border border-slate-300 hover:bg-teal-50 disabled:opacity-40"
                    >
                      קבע כמייצג
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() => onMoveMember(member)}
                    className="text-xs px-2 py-1 rounded border border-slate-300 hover:bg-teal-50 disabled:opacity-40"
                  >
                    העבר...
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      </div>
    </div>
  );
}
