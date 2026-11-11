import { Bidi, countText, formatCount, Ltr, Panel, PanelState } from "@shell";
import { GroupBadges, GroupNumbers } from "./GroupRow.jsx";
import { dateRange, formatScore, pickedIn } from "./groups.js";
import { MemberList } from "./MemberList.jsx";
import { Timeline } from "./Timeline.jsx";

// Main column of the groups view: the selected group in full — representative text, every
// member claim, the people and the timeline, entities, contradiction links (click to open
// the other group) and the merge suggestions that involve it.

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

function LinkRow({ link, actionLabel, onOpen, onAction, saving }) {
  return (
    <li className="flex items-start gap-2 rounded border border-slate-200 bg-white p-2">
      <button type="button" className="min-w-0 flex-1 text-start hover:underline" onClick={() => onOpen(link.group_id)}>
        <Bidi as="p" className="line-clamp-2">
          {link.claim_text}
        </Bidi>
        <span className="text-xs text-slate-500">
          <Ltr>{link.group_id}</Ltr>
          {link.manual && <span className="badge badge-sky ms-2">Manual</span>}
        </span>
      </button>
      <button type="button" className="btn btn-sm shrink-0" disabled={saving} onClick={() => onAction(link.group_id)}>
        {actionLabel}
      </button>
    </li>
  );
}

function Contradictions({ group, saving, on }) {
  return (
    <Section title="Contradicts">
      {group.contradicts.length === 0 ? (
        <p className="text-slate-400">No contradiction links.</p>
      ) : (
        <ul className="space-y-1">
          {group.contradicts.map((link) => (
            <LinkRow key={link.group_id} link={link} actionLabel="Remove the link" saving={saving} onOpen={on.open} onAction={(id) => on.link(id, false)} />
          ))}
        </ul>
      )}
      {group.contradicts_rejected.length > 0 && (
        <>
          <p className="mb-1 mt-3 text-xs text-slate-500">Links removed by hand (step 5 will not bring them back):</p>
          <ul className="space-y-1">
            {group.contradicts_rejected.map((link) => (
              <LinkRow key={link.group_id} link={link} actionLabel="Restore" saving={saving} onOpen={on.open} onAction={(id) => on.link(id, true)} />
            ))}
          </ul>
        </>
      )}
    </Section>
  );
}

function Partners({ group, saving, on }) {
  if (group.partners.length === 0) return null;
  return (
    <Section title="Merge suggestions for this group">
      <ul className="space-y-1">
        {group.partners.map((pair) => {
          const other = pair.a.group_id === group.group_id ? pair.b : pair.a;
          return (
            <li key={pair.key} className="rounded border border-amber-200 bg-amber-50/40 p-2">
              <button type="button" className="w-full text-start hover:underline" onClick={() => on.open(other.group_id)}>
                <Bidi as="p" className="line-clamp-2">
                  {other.claim_text}
                </Bidi>
              </button>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                <span>
                  Lowest similarity <Ltr>{formatScore(pair.min_cosine)}</Ltr>
                </span>
                <GroupNumbers group={other} />
                <span className="ms-auto flex gap-1">
                  <button type="button" className="btn btn-sm" disabled={saving} onClick={() => on.mergeWith(other.group_id)}>
                    Merge
                  </button>
                  <button type="button" className="btn btn-sm" disabled={saving} onClick={() => on.notSame(other.group_id)}>
                    Not the same
                  </button>
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

export function GroupDetail({ detail, selectedId, picked, saving, on }) {
  if (!selectedId) return <PanelState empty emptyText="Select a group in the list" />;
  const group = detail.data?.group;
  return (
    <PanelState loading={detail.loading} error={detail.error} hasData={Boolean(group)} onRetry={detail.reload}>
      {group && (
        <Panel
          title={<Ltr>{group.group_id}</Ltr>}
          subtitle={<GroupBadges group={group} />}
          actions={<QueueNav queue={detail.data.queue} onSelect={on.open} />}
        >
          <div className="space-y-6 p-4 text-sm">
            <Section title={group.representative_manual ? "Representative text (chosen by hand)" : "Representative text"}>
              <Bidi as="p" className="text-base leading-relaxed">
                {group.claim_text}
              </Bidi>
            </Section>
            <Section title="People and dates">
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
                <dt className="text-slate-500">Supporters</dt>
                <dd>{countText(group.supporter_count, "different person", formatCount, "different people")}</dd>
                <dt className="text-slate-500">Opposers</dt>
                <dd>{countText(group.opposer_count, "different person", formatCount, "different people")}</dd>
                <dt className="text-slate-500">Dates</dt>
                <dd>
                  <Ltr>{dateRange(group.first_date, group.last_date)}</Ltr>
                </dd>
                <dt className="text-slate-500">Threads</dt>
                <dd>{formatCount(group.thread_count)}</dd>
              </dl>
            </Section>
            <Section title={`Claims in the group (${formatCount(group.size)})`}>
              <MemberList members={group.members} picked={pickedIn(picked, group.group_id)} saving={saving} on={on} />
            </Section>
            <Section title="By month">
              <Timeline months={group.timeline} />
            </Section>
            <Section title="Entities">
              {group.entities.length === 0 ? (
                <p className="text-slate-400">—</p>
              ) : (
                <div className="flex flex-wrap gap-1">
                  {group.entities.map((entity) => (
                    <Bidi key={entity.entity_id} className="badge badge-slate">
                      {entity.name}
                    </Bidi>
                  ))}
                </div>
              )}
            </Section>
            <Contradictions group={group} saving={saving} on={on} />
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
