import { formatCount } from "./format.js";

// Page controls for a list the server pages (`offset` / `limit` / `total` of the response).
//
//   <ItemList ... total={page.total}
//             header={<Pager offset={offset} limit={PAGE_SIZE} total={page.total} onChange={goTo} />} />
//
// `onChange(newOffset)` is called with the first index of the wanted page. Renders nothing
// when everything fits on one page. The arrows are mirrored characters, so they point the
// right way in the right-to-left page.

export function Pager({ offset, limit, total, onChange }) {
  if (total <= limit) return null;
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.ceil(total / limit);
  return (
    <span className="flex items-center gap-1">
      <button
        type="button"
        className="btn btn-sm"
        aria-label="העמוד הקודם"
        disabled={page <= 1}
        onClick={() => onChange(Math.max(0, offset - limit))}
      >
        ‹
      </button>
      <span>
        עמוד {formatCount(page)} מתוך {formatCount(pages)}
      </span>
      <button
        type="button"
        className="btn btn-sm"
        aria-label="העמוד הבא"
        disabled={page >= pages}
        onClick={() => onChange(offset + limit)}
      >
        ›
      </button>
    </span>
  );
}
