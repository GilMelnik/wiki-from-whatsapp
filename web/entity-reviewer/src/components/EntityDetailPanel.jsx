import { useEffect, useState } from "react";
import { memberColor, entityColor } from "../colors";

const PAGE_SIZE = 12;

const STANCE_LABELS = {
  positive: "חיובי",
  negative: "שלילי",
  neutral: "ניטרלי",
  factual: "עובדתי",
};

const SIGNAL_LABELS = {
  co_occur: "אזכור משותף",
  confident_contact: "פרטי קשר",
  string: "דמיון כתיב",
  prefix: "תחילית",
  seed: "רשימת זרע",
  transliteration: "תעתיק",
};

function EntityLink({ entity, saving, onOpenEntity, implicit = false }) {
  const label = entity.canonical_name || entity.name;
  const color = entity.color_index != null ? entityColor(entity.color_index) : null;
  const className = implicit
    ? "border border-dashed rounded px-1 hover:bg-slate-100 disabled:opacity-40"
    : "underline hover:text-slate-700 disabled:opacity-40";

  if (!entity.entity_id) {
    return (
      <span
        className={`${className} text-slate-500`}
        title={implicit ? "מסומן בטענה, לא מוזכר בטקסט" : undefined}
      >
        {label}
      </span>
    );
  }

  return (
    <button
      type="button"
      disabled={saving}
      onClick={() => onOpenEntity(entity.entity_id)}
      className={className}
      style={color ? { color: color.dot } : undefined}
      title={implicit ? "מסומן בטענה, לא מוזכר בטקסט" : label}
    >
      {label}
    </button>
  );
}

function EntityChipList({ entities, label, saving, onOpenEntity, implicit = false }) {
  if (!entities?.length) return null;
  return (
    <span className="text-slate-500">
      {label}{" "}
      {entities.map((entity, idx) => (
        <span key={entity.entity_id || entity.name}>
          {idx > 0 && " · "}
          <EntityLink
            entity={entity}
            saving={saving}
            onOpenEntity={onOpenEntity}
            implicit={implicit}
          />
        </span>
      ))}
    </span>
  );
}

function renderClaimText(text, highlights, { selfColor, onEntityClick }) {
  if (!highlights || highlights.length === 0) return text;
  const out = [];
  let cursor = 0;
  highlights.forEach((seg, i) => {
    if (seg.start > cursor) out.push(text.slice(cursor, seg.start));
    const slice = text.slice(seg.start, seg.end);
    if (seg.kind === "self") {
      out.push(
        <mark
          key={i}
          className="rounded px-0.5 font-semibold"
          style={{ background: selfColor.bg, color: selfColor.dot }}
        >
          {slice}
        </mark>
      );
    } else {
      const color = entityColor(seg.color_index ?? 0);
      out.push(
        <button
          key={i}
          type="button"
          title={seg.canonical_name || seg.name}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onEntityClick?.(seg.entity_id);
          }}
          className="rounded px-0.5 font-semibold underline underline-offset-2 hover:opacity-80"
          style={{ background: color.bg, color: color.dot }}
        >
          {slice}
        </button>
      );
    }
    cursor = seg.end;
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
  onCopyClaims,
  onExcludeClaim,
  onExcludeClaims,
  onDeleteClaim,
  onMerge,
  onSetStatus,
  onFetchMemberClaims,
  onCreateAggregation,
  onSetRepresentative,
  onDecoupleClaim,
  onPrev,
  onNext,
  onOpenEntity,
  saving,
}) {
  // Per-member claim page (offset/claims/count), keyed by member name. Reset
  // whenever a fresh entity payload arrives so paging never leaks across edits.
  const [pages, setPages] = useState({});
  const [aggDialog, setAggDialog] = useState(null);
  const [aggPopup, setAggPopup] = useState(null);

  useEffect(() => {
    setPages({});
    setAggDialog(null);
    setAggPopup(null);
  }, [entity]);

  if (!entity) {
    return (
      <div className="flex-1 flex items-center justify-center text-slate-500 text-sm">
        בחר ישות מהרשימה
      </div>
    );
  }

  const memberClaims = (member) =>
    pages[member.name]?.claims ?? member.sample_claims;
  const memberOffset = (member) => pages[member.name]?.offset ?? 0;
  const memberCount = (member) => pages[member.name]?.count ?? member.count;

  const changePage = async (member, offset) => {
    const data = await onFetchMemberClaims(member.name, offset, PAGE_SIZE);
    setPages((prev) => ({
      ...prev,
      [member.name]: {
        offset: data.offset,
        claims: data.claims,
        count: data.count,
      },
    }));
  };

  const memberSelectedIds = (name) =>
    memberClaims((entity.members || []).find((m) => m.name === name) || {})
      ?.filter((c) => selectedClaims.has(`${name}\u0001${c.claim_id}`))
      .map((c) => c.claim_id) || [];

  const claimTextById = {};
  (entity.members || []).forEach((m) => {
    (memberClaims(m) || []).forEach((c) => {
      claimTextById[c.claim_id] = c.claim_text;
    });
  });

  const selectedClaimIds = Array.from(
    new Set(Array.from(selectedClaims).map((k) => k.split("\u0001")[1]))
  );

  const openAggDialog = () =>
    setAggDialog({
      claimIds: selectedClaimIds,
      representative: selectedClaimIds[0],
    });

  const confirmAggregation = () => {
    if (!aggDialog || aggDialog.claimIds.length < 2) return;
    onCreateAggregation(aggDialog.claimIds, aggDialog.representative);
    setAggDialog(null);
  };

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
                  : entity.status === "ambiguous"
                    ? "דו-משמעי"
                    : "מוצע"}
            </p>
            {entity.merge_signals?.length > 0 && (
              <div className="mt-1 flex flex-wrap items-center gap-1">
                <span className="text-[10px] text-slate-400">מקור איחוד:</span>
                {entity.merge_signals.map((sig) => (
                  <span
                    key={sig}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 border border-slate-200"
                  >
                    {SIGNAL_LABELS[sig] || sig}
                  </span>
                ))}
              </div>
            )}
            {entity.conflict_with?.length > 0 && (
              <p className="mt-1 text-[10px] text-amber-700">
                ייתכן בלבול עם: {entity.conflict_with.join(" · ")}
              </p>
            )}
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
          <button
            type="button"
            disabled={saving || selectedClaimIds.length < 2}
            onClick={openAggDialog}
            className="text-xs px-3 py-1.5 rounded border border-indigo-300 text-indigo-700 hover:bg-indigo-50 disabled:opacity-40"
            title="אחד טענות נבחרות לכדי טענה מייצגת אחת"
          >
            אחד טענות נבחרות ({selectedClaimIds.length})
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
                    onClick={() => onCopyClaims(member.name, selectedIds)}
                    className="text-[11px] px-2 py-1 rounded border border-slate-300 bg-white/70 hover:bg-white disabled:opacity-40"
                  >
                    העתק טענות ({selectedIds.length})
                  </button>
                  <button
                    type="button"
                    disabled={saving || selectedIds.length === 0}
                    onClick={() => onMoveClaims(member.name, selectedIds)}
                    className="text-[11px] px-2 py-1 rounded border border-slate-300 bg-white/70 hover:bg-white disabled:opacity-40"
                  >
                    הזז טענות ({selectedIds.length})
                  </button>
                  <button
                    type="button"
                    disabled={saving || selectedIds.length === 0}
                    onClick={() => onExcludeClaims(member.name, selectedIds)}
                    className="text-[11px] px-2 py-1 rounded border border-rose-200 text-rose-700 bg-white/70 hover:bg-rose-50 disabled:opacity-40"
                  >
                    לא קשור ({selectedIds.length})
                  </button>
                </div>
              </header>

              <div className="p-2 space-y-1.5">
                {memberClaims(member).map((claim) => {
                  const key = `${member.name}\u0001${claim.claim_id}`;
                  const checked = selectedClaims.has(key);
                  return (
                    <div
                      key={claim.claim_id}
                      className="flex items-start gap-2 bg-white/70 rounded p-2"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => onToggleClaim(member.name, claim.claim_id)}
                        className="mt-1 shrink-0"
                      />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm leading-relaxed">
                          {renderClaimText(claim.claim_text, claim.highlights, {
                            selfColor: color,
                            onEntityClick: onOpenEntity,
                          })}
                        </p>
                        <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                          <code>{claim.claim_id}</code>
                          {claim.stance && (
                            <span>{STANCE_LABELS[claim.stance] || claim.stance}</span>
                          )}
                          {claim.aggregation && (
                            <button
                              type="button"
                              onClick={() => setAggPopup(claim.aggregation)}
                              className="px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700 border border-indigo-200 hover:bg-indigo-200"
                              title="אוחד מטענות נוספות - לחץ לצפייה"
                            >
                              אוחד מ-{claim.aggregation.count} טענות
                            </button>
                          )}
                          {(claim.other_entities || []).length > 0 && (
                            <EntityChipList
                              entities={claim.other_entities}
                              label="ישויות נוספות:"
                              saving={saving}
                              onOpenEntity={onOpenEntity}
                            />
                          )}
                          {(claim.related_entities || []).length > 0 && (
                            <EntityChipList
                              entities={claim.related_entities}
                              label="קשור ללא אזכור:"
                              saving={saving}
                              onOpenEntity={onOpenEntity}
                              implicit
                            />
                          )}
                        </div>
                      </div>
                      <div className="shrink-0 flex flex-col gap-1">
                        <button
                          type="button"
                          disabled={saving}
                          onClick={() => onExcludeClaim(member.name, claim.claim_id)}
                          className="text-[10px] px-2 py-1 rounded border border-rose-200 text-rose-700 hover:bg-rose-50 disabled:opacity-40"
                          title="הטענה לא קשורה לישות זו"
                        >
                          לא קשור
                        </button>
                        <button
                          type="button"
                          disabled={saving}
                          onClick={() => onDeleteClaim(claim.claim_id)}
                          className="text-[10px] px-2 py-1 rounded bg-rose-600 text-white hover:bg-rose-700 disabled:opacity-40"
                          title="מחק את הטענה לחלוטין מכל הצנרת"
                        >
                          מחק
                        </button>
                      </div>
                    </div>
                  );
                })}
                {memberClaims(member).length === 0 && (
                  <p className="text-xs text-slate-400 p-2">אין טענות לדוגמה.</p>
                )}
                {memberCount(member) > PAGE_SIZE && (
                  <div className="flex items-center justify-center gap-3 pt-1 text-[11px] text-slate-500">
                    <button
                      type="button"
                      disabled={saving || memberOffset(member) === 0}
                      onClick={() =>
                        changePage(member, memberOffset(member) - PAGE_SIZE)
                      }
                      className="px-2 py-1 border rounded disabled:opacity-30"
                    >
                      הקודם
                    </button>
                    <span>
                      {memberOffset(member) + 1}–
                      {Math.min(memberOffset(member) + PAGE_SIZE, memberCount(member))}{" "}
                      מתוך {memberCount(member)}
                    </span>
                    <button
                      type="button"
                      disabled={
                        saving ||
                        memberOffset(member) + PAGE_SIZE >= memberCount(member)
                      }
                      onClick={() =>
                        changePage(member, memberOffset(member) + PAGE_SIZE)
                      }
                      className="px-2 py-1 border rounded disabled:opacity-30"
                    >
                      הבא
                    </button>
                  </div>
                )}
              </div>
            </section>
          );
        })}
      </div>

      {aggDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-lg max-h-[80vh] flex flex-col">
            <div className="px-4 py-3 border-b">
              <h3 className="text-sm font-semibold">
                אחד {aggDialog.claimIds.length} טענות
              </h3>
              <p className="text-xs text-slate-500 mt-0.5">
                בחר את הטענה שתישאר כנציג. השאר יוסתרו ויאוחדו בשלב הבא.
              </p>
            </div>
            <div className="flex-1 overflow-y-auto p-3 space-y-1.5">
              {aggDialog.claimIds.map((claimId) => (
                <label
                  key={claimId}
                  className="flex items-start gap-2 bg-slate-50 rounded p-2 cursor-pointer"
                >
                  <input
                    type="radio"
                    name="agg-representative"
                    checked={aggDialog.representative === claimId}
                    onChange={() =>
                      setAggDialog((prev) => ({ ...prev, representative: claimId }))
                    }
                    className="mt-1 shrink-0"
                  />
                  <span className="min-w-0 flex-1 text-sm">
                    {claimTextById[claimId] || claimId}
                    <code className="block text-[11px] text-slate-400">
                      {claimId}
                    </code>
                  </span>
                </label>
              ))}
            </div>
            <div className="px-4 py-3 border-t flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setAggDialog(null)}
                className="text-xs px-3 py-1.5 rounded border border-slate-300 hover:bg-slate-50"
              >
                ביטול
              </button>
              <button
                type="button"
                disabled={saving}
                onClick={confirmAggregation}
                className="text-xs px-3 py-1.5 rounded bg-indigo-600 text-white disabled:opacity-40"
              >
                אחד
              </button>
            </div>
          </div>
        </div>
      )}

      {aggPopup && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-lg max-h-[80vh] flex flex-col">
            <div className="px-4 py-3 border-b flex items-center justify-between">
              <h3 className="text-sm font-semibold">
                טענות מאוחדות ({aggPopup.count})
              </h3>
              <button
                type="button"
                onClick={() => setAggPopup(null)}
                className="text-xs text-slate-500 hover:text-slate-800"
              >
                סגור
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-3 space-y-1.5">
              {aggPopup.members.map((m) => {
                const isRep = m.claim_id === aggPopup.representative;
                return (
                  <div
                    key={m.claim_id}
                    className="flex items-start gap-2 bg-slate-50 rounded p-2"
                  >
                    <div className="min-w-0 flex-1 text-sm">
                      {m.claim_text || m.claim_id}
                      <code className="block text-[11px] text-slate-400">
                        {m.claim_id}
                      </code>
                    </div>
                    {isRep ? (
                      <span className="shrink-0 text-[10px] px-2 py-1 rounded bg-indigo-100 text-indigo-700 font-medium">
                        נציג
                      </span>
                    ) : (
                      <button
                        type="button"
                        disabled={saving}
                        onClick={() => {
                          onSetRepresentative(aggPopup.id, m.claim_id);
                          setAggPopup(null);
                        }}
                        className="shrink-0 text-[10px] px-2 py-1 rounded border border-indigo-200 text-indigo-700 hover:bg-indigo-50 disabled:opacity-40"
                      >
                        קבע כנציג
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={saving}
                      onClick={() => {
                        onDecoupleClaim(aggPopup.id, m.claim_id);
                        setAggPopup(null);
                      }}
                      className="shrink-0 text-[10px] px-2 py-1 rounded border border-rose-200 text-rose-700 hover:bg-rose-50 disabled:opacity-40"
                    >
                      נתק
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
