import { Bidi, Panel, PanelState } from "@shell";
import { ClaimCard, ClaimFacts, ConversationLink, EntityNames, KeepSwitch, ReleaseButton } from "./ClaimCard.jsx";
import { claimState, CONVERSATION_LINK_ID, groupPeopleText, whyText } from "./withheld.js";

// The main column. Released: the selected entity's groups, each with the English label the
// model gave the complaint they share and its claims; or the claims the owner released by
// hand. Still withheld: the selected claim, the entities it names, why nothing releases it,
// and the owner's "Release" (or, once released, "Withhold again").

export function EntityGroups({ entity, minSupporters, saving, onKeep }) {
  return (
    <Panel title={entity ? <Bidi>{entity.entity_name}</Bidi> : "No entity selected"} subtitle="The groups the check found: claims that make the same complaint">
      <PanelState empty={!entity} emptyText="Pick an entity in the list">
        {entity && (
          <div className="space-y-6 p-4">
            {entity.groups.map((group) => (
              <section key={group.claims.map((claim) => claim.short_id).join(" ")} className="space-y-2">
                <h3 className="section-title">{group.label || "(no label)"}</h3>
                <p className="text-xs text-slate-500">{groupPeopleText(group, minSupporters)}</p>
                <ul className="space-y-2">
                  {group.claims.map((claim) => (
                    <ClaimCard key={claim.short_id} claim={claim} saving={saving} onKeep={onKeep} />
                  ))}
                </ul>
              </section>
            ))}
            <p className="text-xs text-slate-500">
              A claim is released when the claims of its group are backed by at least {minSupporters} different people. “Keep withheld” keeps it off every
              page, and it no longer backs the other claims of its group.
            </p>
          </div>
        )}
      </PanelState>
    </Panel>
  );
}

export function ReleasedByYou({ claims, saving, onRelease }) {
  return (
    <Panel title="Released by you" subtitle="Claims the rule withholds that you released by hand: the pages may use them">
      <PanelState empty={claims.length === 0} emptyText="You released no claim by hand. Release one in the “Still withheld” view.">
        <ul className="space-y-2 p-4">
          {claims.map((claim) => (
            <ClaimCard key={claim.short_id} claim={claim} saving={saving} onRelease={onRelease} />
          ))}
        </ul>
      </PanelState>
    </Panel>
  );
}

function StateNote({ claim, minSupporters }) {
  switch (claimState(claim)) {
    case "by_you":
      return <p className="note note-success">You released it by hand: the pages may use it.</p>;
    case "released":
      return <p className="note note-success">The check releases it now that you took your “keep withheld” mark away.</p>;
    default:
      return <p className="note note-warning">{whyText(claim.why, minSupporters)}</p>;
  }
}

export function ClaimDetail({ claim, minSupporters, saving, onKeep, onRelease, releaseKey }) {
  return (
    <Panel title="Withheld claim">
      <PanelState empty={!claim} emptyText="Pick a claim in the list">
        {claim && (
          <div className="space-y-4 p-4 text-sm">
            <Bidi as="p" className="leading-relaxed text-slate-900">
              {claim.text}
            </Bidi>
            <ClaimFacts claim={claim} />
            <EntityNames claim={claim} />
            <StateNote claim={claim} minSupporters={minSupporters} />
            <div className="flex flex-wrap items-center gap-4">
              <ReleaseButton claim={claim} published={!claim.withheld} saving={saving} onRelease={onRelease} keyName={releaseKey} />
              {claim.kept && <KeepSwitch claim={claim} saving={saving} onKeep={onKeep} />}
              <ConversationLink claim={claim} id={CONVERSATION_LINK_ID} />
            </div>
          </div>
        )}
      </PanelState>
    </Panel>
  );
}
