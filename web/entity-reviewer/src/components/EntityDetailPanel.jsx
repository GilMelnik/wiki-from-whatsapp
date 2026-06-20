import { memberColor } from "../colors";

const STANCE_LABELS = {
  positive: "חיובי",
  negative: "שלילי",
  neutral: "ניטרלי",
  factual: "עובדתי",
};

function renderHighlighted(text, spans) {
  if (!spans || spans.length === 0) return text;
  const sorted = [...spans].sort((a, b) => a[0] - b[0]);
  const out = [];
  let cursor = 0;
  sorted.forEach(([start, end], i) => {
    if (start < cursor) return;
    if (start > cursor) out.push(text.slice(cursor, start));
    out.push(
      <mark key={i} className="alias">
        {text.slice(start, end)}
      </mark>
    );
    cursor = end;
  });
  if (cursor < text.length) out.push(text.slice(cursor));
  return out;
}

export default function EntityDetailPanel({
  entity,
  queue,
  selectedClaims,
  onToggleClaim,
  onSetCanonical,
  onMoveMember,
  onMoveClaims,
  onMerge,
  onSetStatus,
  onPrev,
  onNext,
  saving,
}) {
  if (!entity) {
    return (
      <div className="flex-1 flex items-center justify-center text-slate-500 text-sm">
        בחר ישות מהרשימה
      </div>
    );
  }

  const memberSelectedIds = (name) =>
    (entity.members || [])
      .find((m) => m.name === name)
      ?.sample_claims.filter((c) => selectedClaims.has(`${name}\u0001${c.claim_id}`))
      .map((c) => c.claim_id) || [];

  const contacts = entity.contacts || {};
  const contactLines = [
    ...(contacts.email || []),
    ...(contacts.phone || []),
    ...(contacts.website || []),
  ];

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-3 border-b border-slate-200 bg-white shrink-0">
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <h2 className="text-base font-semibold truncate">
              {entity.canonical_name || "(ללא שם)"}
            </h2>
            <p className="text-xs text-slate-500 mt-0.5">
              {entity.members?.length} שמות · {entity.total_count} אזכורים ·{" "}
              {entity.status === "accepted"
                ? "אושר"
                : entity.status === "rejected"
                  ? "נדחה"
                  : "מוצע"}
            </p>
          </div>
          {queue && (
            <div className="flex items-center gap-2 text-xs text-slate-500 shrink-0">
              <button
                type="button"
                disabled={!queue.prev_id}
                onClick={() => onPrev(queue.prev_id)}
                className="px-2 py-1 border rounded disabled:opacity-30"
              >
                הקודם
              </button>
              <span>
                {queue.position}/{queue.total}
              </span>
              <button
                type="button"
                disabled={!queue.next_id}
                onClick={() => onNext(queue.next_id)}
                className="px-2 py-1 border rounded disabled:opacity-30"
              >
                הבא
              </button>
            </div>
          )}
        </div>

        <div className="mt-2 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={saving}
            onClick={() => onSetStatus("accepted")}
            className="text-xs px-3 py-1.5 rounded bg-teal-600 text-white disabled:opacity-40"
          >
            אישור
          </button>
          <button
            type="button"
            disabled={saving}
            onClick={() => onSetStatus("rejected")}
            className="text-xs px-3 py-1.5 rounded border border-rose-300 text-rose-700 hover:bg-rose-50 disabled:opacity-40"
          >
            דחייה
          </button>
          <button
            type="button"
            disabled={saving}
            onClick={onMerge}
            className="text-xs px-3 py-1.5 rounded border border-slate-300 hover:bg-amber-50 disabled:opacity-40"
          >
            מזג ישות זו לתוך אחרת...
          </button>
        </div>

        {contactLines.length > 0 && (
          <div className="mt-2 text-[11px] text-slate-600 bg-amber-50 border border-amber-200 rounded p-2">
            <span className="font-medium">פרטי קשר: </span>
            {contactLines.join(" · ")}
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {(entity.members || []).map((member) => {
          const color = memberColor(member.member_index);
          const selectedIds = memberSelectedIds(member.name);
          const isCanonical = member.name === entity.canonical_name;
          return (
            <section
              key={member.name}
              className="rounded-lg border"
              style={{ borderColor: color.border, background: color.bg }}
            >
              <header className="flex items-center justify-between gap-2 px-3 py-2 border-b" style={{ borderColor: color.border }}>
                <div className="flex items-center gap-2 min-w-0">
                  <span
                    className="w-3 h-3 rounded-full shrink-0"
                    style={{ background: color.dot }}
                  />
                  <span className="text-sm font-semibold truncate">{member.name}</span>
                  <span className="text-[11px] text-slate-500 shrink-0">
                    {member.count} אזכורים
                  </span>
                  {isCanonical && (
                    <span className="text-[10px] text-teal-700 font-medium shrink-0">
                      שם קנוני
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  {!isCanonical && (
                    <button
                      type="button"
                      disabled={saving}
                      onClick={() => onSetCanonical(member.name)}
                      className="text-[11px] px-2 py-1 rounded border border-slate-300 bg-white/70 hover:bg-white disabled:opacity-40"
                    >
                      קבע כקנוני
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() => onMoveMember(member.name)}
                    className="text-[11px] px-2 py-1 rounded border border-slate-300 bg-white/70 hover:bg-white disabled:opacity-40"
                  >
                    הזז שם...
                  </button>
                  <button
                    type="button"
                    disabled={saving || selectedIds.length === 0}
                    onClick={() => onMoveClaims(member.name, selectedIds)}
                    className="text-[11px] px-2 py-1 rounded border border-slate-300 bg-white/70 hover:bg-white disabled:opacity-40"
                  >
                    הזז טענות ({selectedIds.length})
                  </button>
                </div>
              </header>

              <div className="p-2 space-y-1.5">
                {member.sample_claims.map((claim) => {
                  const key = `${member.name}\u0001${claim.claim_id}`;
                  const checked = selectedClaims.has(key);
                  return (
                    <label
                      key={claim.claim_id}
                      className="flex items-start gap-2 bg-white/70 rounded p-2 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => onToggleClaim(member.name, claim.claim_id)}
                        className="mt-1 shrink-0"
                      />
                      <div className="min-w-0">
                        <p className="text-sm leading-relaxed">
                          {renderHighlighted(claim.claim_text, claim.spans)}
                        </p>
                        <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-slate-400">
                          <code>{claim.claim_id}</code>
                          {claim.stance && (
                            <span>{STANCE_LABELS[claim.stance] || claim.stance}</span>
                          )}
                        </div>
                      </div>
                    </label>
                  );
                })}
                {member.sample_claims.length === 0 && (
                  <p className="text-xs text-slate-400 p-2">אין טענות לדוגמה.</p>
                )}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
