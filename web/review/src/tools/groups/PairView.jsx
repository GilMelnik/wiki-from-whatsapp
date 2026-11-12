import { Bidi, formatCount, Panel, PanelState } from "@shell";
import { DisputeCard } from "./Disputes.jsx";
import { diffWords, whySuggested } from "./groups.js";
import { EntityNames, GroupBadges, GroupNumbers, PageLine } from "./GroupRow.jsx";
import { MemberList } from "./MemberList.jsx";
import { DisputeWarning, ReleaseNote, useMergeCheck } from "./MergeNotes.jsx";

// Main column of the merge suggestions: do the two groups say the same thing? In the order the
// owner decides: the two texts side by side with the words that differ highlighted, why the
// pair was suggested, what a merge would do beyond that (a dispute between them, a withheld
// group released), then each group in full: its claims with date and stance, entities, page,
// and the disputes it has with other groups.

function Highlighted({ segments, text }) {
  return (
    <Bidi as="p" text={text} className="text-base leading-relaxed">
      {segments.map((segment, index) =>
        segment.same ? (
          <span key={index}>{segment.text}</span>
        ) : (
          <mark key={index} className="rounded bg-amber-200 px-0.5 text-slate-900">
            {segment.text}
          </mark>
        )
      )}
    </Bidi>
  );
}

function Texts({ pair }) {
  const diff = diffWords(pair.a.claim_text, pair.b.claim_text);
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {[
        ["A", "kept in a merge (the older group)", pair.a, diff.a],
        ["B", "joins A in a merge", pair.b, diff.b],
      ].map(([name, role, group, segments]) => (
        <div key={name} className="card space-y-2">
          <p className="text-xs text-slate-500">
            <span className="font-semibold text-slate-700">{name}</span> · {role}
          </p>
          <Highlighted segments={segments} text={group.claim_text} />
          <GroupBadges group={group} />
        </div>
      ))}
    </div>
  );
}

function Side({ name, group, other, onOpen }) {
  // The link between the two groups is in the warning above; here only their other disputes.
  const links = group.contradicts.filter((link) => link.group_id !== other.group_id);
  return (
    <section className="min-w-0 space-y-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="section-title mb-0">Group {name}</h3>
        <button type="button" className="btn btn-sm btn-quiet" onClick={() => onOpen(group.group_id)}>
          Open it in all groups
        </button>
      </header>
      <GroupNumbers group={group} />
      <PageLine group={group} />
      <EntityNames names={group.entity_names} />
      {group.size > 1 && (
        <div>
          <h4 className="section-title">Its claims ({formatCount(group.size)})</h4>
          <MemberList members={group.members} />
        </div>
      )}
      {links.length > 0 && (
        <div>
          <h4 className="section-title">Other disputes it has</h4>
          <ul className="space-y-2">
            {links.map((link) => (
              <DisputeCard key={link.group_id} group={group} link={link} onOpen={onOpen} />
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function Release({ pair, minSupporters, watch }) {
  const involved = pair.a.withheld || pair.b.withheld;
  const check = useMergeCheck(involved ? [pair.a.group_id, pair.b.group_id] : [], watch);
  return <ReleaseNote check={check.data} minSupporters={minSupporters} />;
}

export function PairView({ list, pair, position, minSupporters, watch, onOpen }) {
  return (
    <PanelState
      loading={list.loading}
      error={list.error}
      hasData={Boolean(list.data)}
      onRetry={list.reload}
      empty={!pair}
      emptyText="No suggestion selected"
    >
      {pair && (
        <Panel title="Do the two groups say the same thing?" subtitle={`Suggestion ${formatCount(position + 1)} of ${formatCount(list.data.total)}`}>
          <div className="max-w-5xl space-y-6 p-4 text-sm">
            <Texts pair={pair} />
            <DisputeWarning disputes={pair.dispute ? [pair.dispute] : []} />
            <Release pair={pair} minSupporters={minSupporters} watch={watch} />
            <section>
              <h3 className="section-title">Why suggested</h3>
              <ul className="list-disc space-y-1 ps-5 text-slate-700">
                {whySuggested(pair).map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </section>
            <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
              <Side name="A" group={pair.a} other={pair.b} onOpen={onOpen} />
              <Side name="B" group={pair.b} other={pair.a} onOpen={onOpen} />
            </div>
          </div>
        </Panel>
      )}
    </PanelState>
  );
}
