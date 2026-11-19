import { Bidi, Panel, PanelState } from "@shell";
import { ClaimCard, ClaimFacts, KeepSwitch } from "./ClaimCard.jsx";
import { groupPeopleText, whyText } from "./withheld.js";

// The main column. Released by the check: the selected entity's groups, each with the English
// label the model gave the complaint they share and its claims. Still withheld: the selected
// claim, the entities it names and why nothing releases it.

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

export function ClaimDetail({ claim, minSupporters, saving, onKeep }) {
  return (
    <Panel title="Withheld claim">
      <PanelState empty={!claim} emptyText="Pick a claim in the list">
        {claim && (
          <div className="space-y-4 p-4 text-sm">
            <Bidi as="p" className="leading-relaxed text-slate-900">
              {claim.text}
            </Bidi>
            <ClaimFacts claim={claim} />
            <p>
              Names{" "}
              {claim.entity_names.map((name, index) => (
                <span key={name}>
                  {index > 0 && ", "}“<Bidi>{name}</Bidi>”
                </span>
              ))}
            </p>
            <p className="note note-warning">{whyText(claim.why, minSupporters)}</p>
            {claim.kept && <KeepSwitch claim={claim} saving={saving} onKeep={onKeep} />}
          </div>
        )}
      </PanelState>
    </Panel>
  );
}
