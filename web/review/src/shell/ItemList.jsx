import { useEffect, useRef } from "react";
import { formatCount } from "./format.js";
import { useShortcuts } from "./useShortcuts.js";

// The selectable list of the `list` column.
//
//   <ItemList
//     items={claims} getKey={(claim) => claim.claim_id}
//     selectedKey={selectedId} onSelect={setSelectedId}
//     total={page.total}                       // optional: "200 מתוך 2,710"
//     header={<SortMenu />}                    // optional: shown next to the count
//     prevKey={queue.prev_id} nextKey={queue.next_id}  // optional: neighbours off this page
//     renderItem={(claim, { selected }) => <ClaimRow claim={claim} />}
//   />
//
// j / k move the selection from anywhere on the page; the arrow keys do so while the list
// has the focus (elsewhere they keep scrolling). The selected row is kept in view. In a
// server-paged list, `prevKey` / `nextKey` (the selected item's neighbours in the whole
// queue) let j / k step over the page edge; see usePagedSelection for following the page.

const ARROW_STEPS = { ArrowDown: 1, ArrowUp: -1 };

export function ItemList({
  items,
  getKey,
  selectedKey,
  onSelect,
  renderItem,
  total,
  header,
  prevKey = null,
  nextKey = null,
}) {
  const listRef = useRef(null);
  const selectedRef = useRef(null);
  const index = items.findIndex((item) => getKey(item) === selectedKey);

  const move = (step) => {
    if (items.length === 0) return;
    if (index !== -1 && index + step < 0 && prevKey) return onSelect(prevKey);
    if (index !== -1 && index + step >= items.length && nextKey) return onSelect(nextKey);
    const target = index === -1 ? 0 : Math.min(items.length - 1, Math.max(0, index + step));
    onSelect(getKey(items[target]));
  };
  useShortcuts([
    { keys: "j", label: "הפריט הבא ברשימה", run: () => move(1) },
    { keys: "k", label: "הפריט הקודם ברשימה", run: () => move(-1) },
  ]);

  useEffect(() => {
    const row = selectedRef.current;
    if (!row) return;
    row.scrollIntoView({ block: "nearest" });
    if (listRef.current.contains(document.activeElement)) row.focus();
  }, [selectedKey]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-slate-200 px-3 py-2 text-xs text-slate-500">
        <span>
          {formatCount(items.length)}
          {total !== undefined && total !== items.length && ` מתוך ${formatCount(total)}`}
        </span>
        {header}
      </div>
      <ul
        ref={listRef}
        className="min-h-0 flex-1 divide-y divide-slate-100 overflow-y-auto"
        onKeyDown={(event) => {
          const step = ARROW_STEPS[event.key];
          // Arrows with a modifier belong to the tool's own shortcuts (e.g. Alt+↑ reorders).
          if (!step || event.altKey || event.ctrlKey || event.metaKey) return;
          event.preventDefault();
          move(step);
        }}
      >
        {items.map((item) => {
          const key = getKey(item);
          const selected = key === selectedKey;
          return (
            <li key={key}>
              <button
                type="button"
                ref={selected ? selectedRef : null}
                aria-current={selected ? "true" : undefined}
                onClick={() => onSelect(key)}
                className={`list-row ${selected ? "list-row-selected" : ""}`}
              >
                {renderItem(item, { selected })}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
