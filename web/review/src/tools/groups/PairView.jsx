import { Bidi, formatCount, Ltr, Panel, PanelState } from "@shell";
import { GroupBadges, GroupNumbers } from "./GroupRow.jsx";
import { formatScore } from "./groups.js";
import { MemberList } from "./MemberList.jsx";

// Main column of the suggestions view: the two groups of the current suggestion side by
// side — representative text, every member claim with its own text and numbers, the people
// and dates — and how close they came to step 5's own bar.

function Side({ label, group, onOpen }) {
  return (
    <section className="min-w-0 space-y-3 rounded border border-slate-200 bg-white p-3">
      <header className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-slate-500">{label}</span>
        <button type="button" className="text-xs text-slate-400 hover:underline" title="פתיחה בתצוגת הקבוצות" onClick={() => onOpen(group.group_id)}>
          <Ltr>{group.group_id}</Ltr>
        </button>
      </header>
      <GroupBadges group={group} />
      <Bidi as="p" className="text-base leading-relaxed">
        {group.claim_text}
      </Bidi>
      <GroupNumbers group={group} />
      {group.entity_names.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {group.entity_names.map((name) => (
            <Bidi key={name} className="badge badge-slate">
              {name}
            </Bidi>
          ))}
        </div>
      )}
      {group.size > 1 && (
        <div>
          <h3 className="section-title">הטענות בקבוצה ({formatCount(group.size)})</h3>
          <MemberList members={group.members} />
        </div>
      )}
    </section>
  );
}

function Score({ pair, bars }) {
  const bar = pair.shared_entity ? bars?.shared_entity : bars?.no_shared_entity;
  return (
    <p className="text-sm text-slate-600">
      הדמיון החלש ביותר בין טענה לטענה: <Ltr>{formatScore(pair.min_cosine)}</Ltr>
      {pair.max_cosine !== pair.min_cosine && (
        <>
          {" "}
          (החזק: <Ltr>{formatScore(pair.max_cosine)}</Ltr>)
        </>
      )}{" "}
      · {pair.shared_entity ? "ישות משותפת" : "בלי ישות משותפת"}
      {bar !== undefined && (
        <>
          {" "}
          · שלב 5 ממזג בעצמו מ-<Ltr>{formatScore(bar)}</Ltr> (פער <Ltr>{formatScore(pair.margin, { signed: true })}</Ltr>)
        </>
      )}
    </p>
  );
}

export function PairView({ list, pair, position, onOpen }) {
  return (
    <PanelState
      loading={list.loading}
      error={list.error}
      hasData={Boolean(list.data)}
      onRetry={list.reload}
      empty={!pair}
      emptyText="אין הצעה נבחרת"
    >
      {pair && (
        <Panel
          title={`הצעה ${formatCount(position + 1)} מתוך ${formatCount(list.data.total)}`}
          subtitle="האם שתי הקבוצות אומרות את אותו הדבר?"
        >
          <div className="space-y-4 p-4 text-sm">
            <Score pair={pair} bars={list.data.bars} />
            <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
              <Side label="קבוצה א (הוותיקה, נשמרת במיזוג)" group={pair.a} onOpen={onOpen} />
              <Side label="קבוצה ב" group={pair.b} onOpen={onOpen} />
            </div>
          </div>
        </Panel>
      )}
    </PanelState>
  );
}
