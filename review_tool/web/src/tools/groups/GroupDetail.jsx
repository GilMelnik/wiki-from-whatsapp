import { Bidi, countText, formatCount, Ltr, Panel, PanelState } from "@shell";
import { Disputes } from "./Disputes.jsx";
import { dateRange, pickedIn } from "./groups.js";
import { EntityNames, GroupBadges, GroupNumbers, PageLine } from "./GroupRow.jsx";
import { MemberList } from "./MemberList.jsx";
import { Timeline } from "./Timeline.jsx";

// Main column of a groups list: the selected group in full, in the order the owner judges it:
// whether the wiki shows it (withheld: why, and what releases it), its text, both sides of
// every dispute it has, its member claims, people and dates, the timeline, its entities and
// the merge suggestions that involve it (no similarity numbers: the pair view says why).

function Section({ title, children }) {
  return (
    <section>
      <h3 className="section-title">{title}</h3>
      {children}
    </section>
  );
}

function QueueNav({ queue, onSelect }) {
  if (!queue || queue.position === null) return null;
  return (
    <>
      <button type="button" className="btn btn-sm" disabled={!queue.prev_id} onClick={() => onSelect(queue.prev_id)}>
        ‹ Previous
      </button>
      <span className="text-xs text-slate-500">
        {formatCount(queue.position + 1)} / {formatCount(queue.total)}
      </span>
      <button type="button" className="btn btn-sm" disabled={!queue.next_id} onClick={() => onSelect(queue.next_id)}>
        Next ›
      </button>
    </>
  );
}

/** Why the rule keeps the group off the wiki, and the two ways it gets published. */
function Withheld({ group, minSupporters }) {
  const names = group.entities.map((entity) => entity.name);
  return (
    <div className="note note-warning space-y-2">
      <p className="font-medium">Withheld from the wiki</p>
      <p>
        A negative statement that names{" "}
        {names.map((name, index) => (
          <span key={name}>
            {index > 0 && ", "}“<Bidi>{name}</Bidi>”
          </span>
        ))}
        , {group.supporter_count > 0 ? `reported by only ${countText(group.supporter_count, "member")}` : "with no member counted as reporting it"}: the wiki publishes such a statement only when at least{" "}
        {countText(minSupporters ?? 2, "member")} reported it, so it is kept off every page
        {group.page ? (
          <>
            {" "}
            (it would be on “<Bidi>{group.page.title_he}</Bidi>”)
          </>
        ) : null}
        .
      </p>
      <p className="text-xs">
        It is released by itself when another member reports the same, or when you merge it with a group that says the same
        thing{group.partners.length > 0 ? " (the merge suggestions for it are below)" : ""}. There is no other way to release it.
      </p>
    </div>
  );
}

function LinkActions({ link, saving, on, restore = false }) {
  return (
    <button type="button" className="btn btn-sm" disabled={saving} onClick={() => on.link(link.group_id, restore)}>
      {restore ? "Restore the link" : "Remove the link"}
    </button>
  );
}

function BothSides({ group, saving, on }) {
  if (group.contradicts.length === 0 && group.contradicts_rejected.length === 0) return null;
  return (
    <Section title="Both sides">
      <Disputes group={group} onOpen={on.open} actionsFor={(link) => <LinkActions link={link} saving={saving} on={on} />} />
      {group.contradicts_rejected.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-xs text-slate-500">Links you removed (step 5 will not bring them back)</summary>
          <Disputes
            group={{ ...group, contradicts: group.contradicts_rejected }}
            onOpen={on.open}
            actionsFor={(link) => <LinkActions link={link} saving={saving} on={on} restore />}
          />
        </details>
      )}
    </Section>
  );
}

function Partners({ group, saving, on }) {
  if (group.partners.length === 0) return null;
  return (
    <Section title="Merge suggestions for this group">
      <ul className="space-y-2">
        {group.partners.map((pair) => {
          const other = pair.a.group_id === group.group_id ? pair.b : pair.a;
          return (
            <li key={pair.key} className="card space-y-2">
              <button type="button" className="w-full text-start hover:underline" onClick={() => on.open(other.group_id)}>
                <Bidi as="span" className="line-clamp-3 block">
                  {other.claim_text}
                </Bidi>
              </button>
              <span className="flex flex-wrap items-center gap-2">
                <GroupBadges group={other} />
                <GroupNumbers group={other} />
                <span className="ms-auto flex gap-1">
                  <button type="button" className="btn btn-sm" disabled={saving} onClick={() => on.mergeWith(pair)}>
                    {pair.dispute ? "The same – merge…" : "The same – merge"}
                  </button>
                  <button type="button" className="btn btn-sm" disabled={saving} onClick={() => on.notSame(other.group_id)}>
                    Not the same
                  </button>
                </span>
              </span>
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

export function GroupDetail({ detail, selectedId, picked, saving, minSupporters, on }) {
  if (!selectedId) return <PanelState empty emptyText="Select a group in the list" />;
  const group = detail.data?.group;
  return (
    <PanelState loading={detail.loading} error={detail.error} hasData={Boolean(group)} onRetry={detail.reload}>
      {group && (
        <Panel
          title={
            <span className="flex flex-wrap items-center gap-2">
              Claim group <Ltr className="font-normal text-slate-400">{group.group_id}</Ltr>
            </span>
          }
          subtitle={<GroupBadges group={group} />}
          actions={<QueueNav queue={detail.data.queue} onSelect={on.open} />}
        >
          <div className="max-w-4xl space-y-6 p-4 text-sm">
            {group.withheld && <Withheld group={group} minSupporters={minSupporters} />}
            <Section title={group.representative_manual ? "What it says (the text you chose)" : "What it says"}>
              <Bidi as="p" className="card text-base leading-relaxed">
                {group.claim_text}
              </Bidi>
            </Section>
            <BothSides group={group} saving={saving} on={on} />
            <Section title={group.size > 1 ? `Its claims (${formatCount(group.size)})` : "Its claim"}>
              <MemberList members={group.members} picked={pickedIn(picked, group.group_id)} saving={saving} on={on} />
            </Section>
            <Section title="People, dates and page">
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
                <dt className="text-slate-500">Supporters</dt>
                <dd>{countText(group.supporter_count, "different person", formatCount, "different people")}</dd>
                <dt className="text-slate-500">Opposers</dt>
                <dd>{countText(group.opposer_count, "different person", formatCount, "different people")}</dd>
                <dt className="text-slate-500">Dates</dt>
                <dd>
                  <Ltr>{dateRange(group.first_date, group.last_date)}</Ltr>
                </dd>
                <dt className="text-slate-500">Conversations</dt>
                <dd>{formatCount(group.thread_count)}</dd>
                <dt className="text-slate-500">Page</dt>
                <dd>
                  <PageLine group={group} />
                </dd>
              </dl>
            </Section>
            <Section title="By month">
              <Timeline months={group.timeline} />
            </Section>
            {group.entities.length > 0 && (
              <Section title="Entities">
                <EntityNames names={group.entities.map((entity) => entity.name)} />
              </Section>
            )}
            <Partners group={group} saving={saving} on={on} />
            {group.merged_from.length > 0 && (
              <Section title="Groups merged into it">
                <p className="flex flex-wrap gap-2">
                  {group.merged_from.map((id) => (
                    <Ltr key={id} className="text-slate-500">
                      {id}
                    </Ltr>
                  ))}
                </p>
              </Section>
            )}
          </div>
        </Panel>
      )}
    </PanelState>
  );
}
