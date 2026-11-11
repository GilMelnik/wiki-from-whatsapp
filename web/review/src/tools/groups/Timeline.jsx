import { formatCount, Ltr } from "@shell";
import { barPercent } from "./groups.js";

// The group's people per month (distinct people; a person on both sides counts on the side
// of their latest event that month). A table with a bar per side, so the numbers are always
// readable and the two sides are told apart by column, not only by color.

function Bar({ value, max, tone, label }) {
  return (
    <div className="flex items-center gap-2" title={`${label}: ${value}`}>
      <div className="h-2 w-24 shrink-0 rounded bg-slate-100">
        <div className={`h-2 rounded ${tone}`} style={{ width: `${barPercent(value, max)}%` }} />
      </div>
      <span className="w-6 text-xs tabular-nums text-slate-600">{formatCount(value)}</span>
    </div>
  );
}

export function Timeline({ months }) {
  if (months.length === 0) return <p className="text-slate-400">No dated support or opposition events.</p>;
  const max = Math.max(...months.map((month) => Math.max(month.supporters, month.opposers)));
  return (
    <table className="text-sm">
      <thead>
        <tr className="text-xs text-slate-500">
          <th className="pe-4 text-start font-normal">Month</th>
          <th className="pe-4 text-start font-normal">
            <span className="me-1 inline-block h-2 w-2 rounded-sm bg-teal-600" />
            Supporters
          </th>
          <th className="text-start font-normal">
            <span className="me-1 inline-block h-2 w-2 rounded-sm bg-rose-600" />
            Opposers
          </th>
        </tr>
      </thead>
      <tbody>
        {months.map((month) => (
          <tr key={month.month}>
            <td className="pe-4">
              <Ltr>{month.month}</Ltr>
            </td>
            <td className="pe-4">
              <Bar value={month.supporters} max={max} tone="bg-teal-600" label="Supporters" />
            </td>
            <td>
              <Bar value={month.opposers} max={max} tone="bg-rose-600" label="Opposers" />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
