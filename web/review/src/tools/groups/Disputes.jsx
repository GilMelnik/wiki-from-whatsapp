import { Bidi, countText, formatCount, Ltr, stanceOf } from "@shell";
import { dateRange, ORIGIN_LABELS } from "./groups.js";
import { GroupBadges, PageLine } from "./GroupRow.jsx";

// Both sides of a dispute: for each contradiction link of a group, what this group says next
// to what the other side says (each with its supporters and dates), where the link comes
// from and, when members disputed each other in a conversation, which claim disputes which.
// The counts of the two sides are never added up: one side's opposers are mostly the other
// side's supporters.

function Side({ label, group }) {
  return (
    <div className="min-w-0 space-y-1">
      <p className="text-xs font-medium text-slate-600">{label}</p>
      <Bidi as="p" className="leading-relaxed">
        {group.claim_text}
      </Bidi>
      <p className="flex flex-wrap gap-x-3 text-xs text-slate-500">
        <span>{countText(group.supporter_count, "supporter", formatCount)}</span>
        <Ltr>{dateRange(group.first_date, group.last_date)}</Ltr>
      </p>
    </div>
  );
}

function Claim({ member }) {
  if (member.missing) return <p className="text-slate-400">No longer in the claims file.</p>;
  const stance = stanceOf(member.stance);
  return (
    <div className="space-y-0.5">
      <Bidi as="p">{member.claim_text}</Bidi>
      <p className="flex flex-wrap items-center gap-2 text-slate-500">
        <span className={`badge ${stance.badge}`}>{stance.label}</span>
        <Ltr>{dateRange(member.first_date, member.last_date)}</Ltr>
      </p>
    </div>
  );
}

function WhoDisputesWhat({ pairs }) {
  return (
    <section>
      <h4 className="section-title">Who disputes what</h4>
      <ul className="space-y-2 text-xs">
        {pairs.map((pair) => (
          <li key={`${pair.ours.claim_id}|${pair.theirs.claim_id}`} className="grid gap-3 border-s-2 border-rose-200 ps-2 md:grid-cols-2">
            <Claim member={pair.ours} />
            <Claim member={pair.theirs} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The claim pairs say more than the two group texts (a group holds several claims). */
function pairsAddSomething(group, link) {
  return (link.pairs ?? []).some((pair) => pair.ours.claim_text !== group.claim_text || pair.theirs.claim_text !== link.claim_text);
}

/** One contradiction link of `group`. `actions`: buttons for the link (remove, restore). */
export function DisputeCard({ group, link, onOpen, actions = null }) {
  if (link.missing) {
    return (
      <li className="card text-xs text-slate-500">
        The other side, group <Ltr>{link.group_id}</Ltr>, is no longer in the groups file: the link goes at the next
        change here or the next step 5 run.
      </li>
    );
  }
  return (
    <li className="card space-y-3">
      {link.origin && <p className="text-xs text-slate-500">{ORIGIN_LABELS[link.origin]}</p>}
      <div className="grid gap-4 md:grid-cols-2">
        <Side label="This group says" group={group} />
        <div className="space-y-1">
          <Side label="The other side says" group={link} />
          <span className="flex flex-wrap items-center gap-2">
            <GroupBadges group={link} />
            <PageLine group={link} />
          </span>
        </div>
      </div>
      {pairsAddSomething(group, link) && <WhoDisputesWhat pairs={link.pairs} />}
      <p className="flex flex-wrap gap-2">
        <button type="button" className="btn btn-sm" onClick={() => onOpen(link.group_id)}>
          Open the other side
        </button>
        {actions}
      </p>
    </li>
  );
}

/** Every contradiction link of `group`, or nothing when it has none. */
export function Disputes({ group, onOpen, actionsFor }) {
  if (!group.contradicts?.length) return null;
  return (
    <ul className="space-y-2">
      {group.contradicts.map((link) => (
        <DisputeCard key={link.group_id} group={group} link={link} onOpen={onOpen} actions={actionsFor?.(link)} />
      ))}
    </ul>
  );
}
