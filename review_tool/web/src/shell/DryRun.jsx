import { Consequences } from "./OperationDialog.jsx";
import { usePreview } from "./planApi.js";

// The dry run of a plan operation, in view next to a proposal before anyone decides: the plan
// tool runs the operation on its current plan without saving and the block lists what it
// would mean downstream (what disappears, is redirected, is classified again), or, verbatim,
// which plan rules it would break. `overview` = the plan as GET /api/plan/plan answers it
// (usePlanOverview, or the plan tool's own); `op` = null while there is nothing to run.
//
//   <DryRun op={{ op: "add_label", ... }} overview={plan} />

export function DryRun({ op, overview, title = "What applying it does to the plan" }) {
  const { preview, error, pending } = usePreview(op);
  return (
    <section>
      <h3 className="section-title">{title}</h3>
      <Consequences preview={preview} error={error} pending={pending} overview={overview} />
    </section>
  );
}
