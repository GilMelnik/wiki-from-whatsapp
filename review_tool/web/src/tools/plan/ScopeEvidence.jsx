import { Bidi, countText, formatCount, claimCount, stanceOf, titleOf } from "@shell";
import { percent } from "./plan.js";

// What a page's scope is judged by, next to the scope editor: the page's exact size against
// the planning guide (once step 6b has run), a sample of the claims on it (least certain
// first), the claims the classifier found closest to it that it did not put there, the
// example claims the planning model gave, and (folded) the planning run's coverage numbers.
// From GET /pages/{id}. The size flag itself is one of the page's warnings, above the scope.

function Section({ title, children }) {
  return (
    <section>
      <h3 className="section-title">{title}</h3>
      {children}
    </section>
  );
}

function Where({ row, overview }) {
  const pages = row.pages ?? [];
  if (!row.record) return <span>not assigned yet</span>;
  if (pages.length === 0) return <span>on no page</span>;
  return (
    <span>
      now on “<Bidi>{titleOf(overview, pages[0])}</Bidi>”
    </span>
  );
}

/** One claim, read only: its text, how sure the classifier was, its marks. */
function ClaimLine({ row, overview, showWhere = false }) {
  if (row.missing) return <li className="text-xs text-slate-500">The claim is no longer among the claims (deleted or re-extracted).</li>;
  const stance = stanceOf(row.stance);
  return (
    <li className="card space-y-1 p-2" title={row.short_id}>
      <Bidi as="p" className="leading-relaxed">
        {row.claim_text}
      </Bidi>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
        {row.certainty !== null && row.certainty !== undefined && (
          <span title="The probability the classifier gave this page">{percent(row.certainty)} sure</span>
        )}
        <span className={`badge ${stance.badge}`}>{stance.label}</span>
        <span>{countText(row.supporter_count ?? 0, "supporter", formatCount)}</span>
        {row.withheld && <span className="badge badge-amber">Withheld from the wiki</span>}
        {showWhere && <Where row={row} overview={overview} />}
      </p>
    </li>
  );
}

function ClaimLines({ rows, overview, showWhere, empty }) {
  if (rows.length === 0) return <p className="text-slate-500">{empty}</p>;
  return (
    <ul className="space-y-2">
      {rows.map((row) => (
        <ClaimLine key={row.short_id} row={row} overview={overview} showWhere={showWhere} />
      ))}
    </ul>
  );
}

function Size({ size, coverage }) {
  if (size) {
    return (
      <p>
        The main page of <span className="font-semibold">{claimCount(size.claims, formatCount)}</span>
        {size.flag === null && ", inside the planning guide"}. Claims withheld from the wiki do not count.
      </p>
    );
  }
  if (coverage?.measured) {
    return (
      <p className="text-slate-600">
        About {claimCount(coverage.projected + coverage.merged_projected, formatCount)}, projected from the planning
        sample. The exact size comes with step 6b.
      </p>
    );
  }
  return <p className="text-slate-500">Known once step 6b has assigned the claims.</p>;
}

function Examples({ examples, claimsAvailable }) {
  if (!claimsAvailable) return <p className="text-slate-500">The claims could not be loaded (no claims or no audit file yet), so there is no text to show.</p>;
  if (examples.length === 0) return <p className="text-slate-500">The model gave no example claims for the page.</p>;
  return <ClaimLines rows={examples} empty="" />;
}

function Coverage({ coverage, report }) {
  if (!report) return <p className="text-slate-500">No coverage report (it is made at planning time).</p>;
  if (!coverage?.measured) return <p className="text-slate-500">The page was added after the coverage check, so it has no numbers.</p>;
  return (
    <div className="space-y-1 text-slate-600">
      <p>
        {formatCount(coverage.count + coverage.merged_count)} of the {claimCount(report.sample_size, formatCount)} in the
        sample were assigned to the page
        {coverage.merged_count > 0 && ` (with ${formatCount(coverage.merged_count)} of pages merged into it)`}.
      </p>
      {coverage.scope_changed && <p className="text-xs text-amber-800">The scope changed since the check; the numbers describe the previous scope.</p>}
    </div>
  );
}

export function ScopeEvidence({ overview, detail, onClaims }) {
  const data = detail.data;
  const assigned = data.size !== null;
  return (
    <div className="space-y-6">
      <Section title="Size">
        <Size size={data.size} coverage={data.coverage} />
      </Section>
      {assigned && (
        <>
          <Section title="On the page, least certain first">
            <ClaimLines rows={data.sample} overview={overview} empty="No claim has the page as its main page." />
            {data.size.claims > data.sample.length && (
              <button type="button" className="btn btn-sm mt-2" onClick={onClaims}>
                All its claims
              </button>
            )}
          </Section>
          <Section title="The closest claims it did not get">
            <ClaimLines rows={data.near} overview={overview} showWhere empty="The classifier gave the page to no other claim." />
          </Section>
        </>
      )}
      {assigned ? (
        <details>
          <summary className="cursor-pointer text-xs font-semibold text-slate-500">Examples the planning model gave</summary>
          <div className="mt-2">
            <Examples examples={data.examples} claimsAvailable={overview.claims_available} />
          </div>
        </details>
      ) : (
        <Section title="Examples the planning model gave">
          <Examples examples={data.examples} claimsAvailable={overview.claims_available} />
        </Section>
      )}
      <details>
        <summary className="cursor-pointer text-xs font-semibold text-slate-500">Coverage check of the planning run</summary>
        <div className="mt-2">
          <Coverage coverage={data.coverage} report={overview.coverage} />
        </div>
      </details>
    </div>
  );
}
