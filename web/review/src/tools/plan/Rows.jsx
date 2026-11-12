import { Bidi, countText, formatCount, Ltr } from "@shell";
import { PROPOSAL_KINDS } from "./assign.js";
import { ChangeBadge } from "./bits.jsx";
import { warningsFor } from "./plan.js";

// Rows of the list column: the tree (indented by depth, with the assignment lists on top and,
// under the proposals, each proposal still to decide) and the labels (grouped by kind). A row
// carries only what calls for a look: "New" / "Edited", a mark when a warning names it, the
// size of a to-do list. Ids are in the tooltip.

const ASSIGNMENT_ROWS = {
  unplaced: { title: "Groups on no page", count: (summary) => summary.counts.unplaced },
  waiting: { title: "Waiting for the assignment run", count: (summary) => summary.counts.waiting },
  proposals: { title: "Proposals to change the plan", count: (summary) => summary.proposals.pending },
};

function AssignmentRow({ overview, item }) {
  const row = ASSIGNMENT_ROWS[item.kind];
  const summary = overview.assignments;
  const count = summary?.exists && !summary.problem ? row.count(summary) : null;
  return (
    <span className="flex items-center gap-2 text-slate-700">
      <span>{row.title}</span>
      {count === null ? (
        <span className="ms-auto text-xs text-slate-500">6b has not run</span>
      ) : (
        count > 0 && (
          <span className="badge badge-amber ms-auto" title={item.kind === "proposals" ? "Waiting for a decision" : "Claim groups"}>
            {formatCount(count)}
          </span>
        )
      )}
    </span>
  );
}

function ProposalRow({ item }) {
  return (
    <span className="flex items-start gap-2 ps-4" title={item.proposal.id}>
      <span className="badge badge-slate shrink-0">{PROPOSAL_KINDS[item.proposal.kind] ?? item.proposal.kind}</span>
      <Bidi className="min-w-0">{item.proposal.title_he}</Bidi>
    </span>
  );
}

const INDENT_REM = 1;

/** A mark when a warning names the item (worth-a-look notes only in the tooltip). */
function WarningMark({ overview, id }) {
  const warnings = warningsFor(overview, id);
  if (warnings.length === 0) return null;
  const strong = warnings.some((warning) => (warning.tone ?? "warning") === "warning");
  return (
    <span className={`badge ${strong ? "badge-amber" : "badge-slate"}`} title={warnings.map((warning) => warning.text).join("\n")}>
      {strong ? "!" : "?"}
    </span>
  );
}

export function TreeRow({ overview, item }) {
  if (ASSIGNMENT_ROWS[item.kind]) return <AssignmentRow overview={overview} item={item} />;
  if (item.kind === "proposal") return <ProposalRow item={item} />;
  if (item.kind === "overview") return <span className="font-semibold">Plan overview</span>;
  const isPage = item.kind === "page";
  return (
    <span className="flex items-center gap-1.5" style={{ paddingInlineStart: `${(item.depth - 1) * INDENT_REM}rem` }} title={item.id}>
      <span aria-hidden className={isPage ? "text-emerald-600" : "text-slate-400"}>
        {isPage ? "•" : "▾"}
      </span>
      <Bidi className={isPage ? "" : "font-semibold text-slate-700"}>{item.title_he}</Bidi>
      <ChangeBadge overview={overview} id={item.id} />
      <WarningMark overview={overview} id={item.id} />
    </span>
  );
}

export function LabelRow({ overview, item }) {
  const finding = overview.assignments?.label_findings?.find((found) => found.label_id === item.id);
  return (
    <span className="block" title={item.id}>
      {item.first && (
        <span className="mb-1 block text-xs font-semibold text-slate-500">
          <Ltr>{item.labelKind}</Ltr>
        </span>
      )}
      <span className="flex items-center gap-1.5 ps-2">
        <Bidi>{item.title_he}</Bidi>
        <ChangeBadge overview={overview} id={item.id} />
        <WarningMark overview={overview} id={item.id} />
        {finding && (
          <span className="badge badge-amber" title={finding.finding === "large" ? "On a very large share of the groups" : "On very few groups"}>
            {finding.finding === "large" ? "Very common" : countText(finding.count, "group", formatCount)}
          </span>
        )}
      </span>
    </span>
  );
}
