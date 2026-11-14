import { useEffect, useRef } from "react";

// Keeping the selection and the page of a server-paged list in step.
//
//   const [offset, setOffset] = useState(0);
//   const list = useResource(() => api.get("/items", { offset, limit: PAGE_SIZE }), [offset], ...);
//   const detail = useResource(...);                 // answers `queue: {position, ...}`
//   const goToPage = usePagedSelection({
//     list, getKey: (item) => item.id, selectedKey, onSelect: setSelectedKey,
//     position: detail.data?.queue.position, lost: detail.error?.status === 404,  // (see below)
//     offset, setOffset, pageSize: PAGE_SIZE,
//   });
//   <Pager ... onChange={goToPage} />
//
// - The list shows the page that holds the selected item: when the item's queue `position`
//   (0-based, from the server) moves to another page, `offset` follows.
// - The selection stays valid: with nothing selected, a `lost` selection (the caller decides:
//   a 404, or the item left the view, `position === null`), or after `goToPage` (also the
//   way to go back to the first page when the filter changes), the first listed item is
//   selected once the list has loaded.

/** First index of the page that shows queue position `position`. */
export function pageStart(position, pageSize) {
  return Math.floor(position / pageSize) * pageSize;
}

export function usePagedSelection({
  list,
  getKey,
  selectedKey,
  onSelect,
  position,
  lost = false,
  offset,
  setOffset,
  pageSize,
}) {
  const selectFirstOnLoad = useRef(false);

  useEffect(() => {
    if (list.loading || !list.data) return;
    if (selectFirstOnLoad.current || !selectedKey || lost) {
      selectFirstOnLoad.current = false;
      const first = list.data.items[0];
      const key = first === undefined ? null : getKey(first);
      if (key !== selectedKey) onSelect(key);
    }
  }, [list.data, list.loading, selectedKey, lost]);

  useEffect(() => {
    if (position === null || position === undefined) return;
    const start = pageStart(position, pageSize);
    if (start !== offset) setOffset(start);
  }, [position]);

  return (nextOffset) => {
    selectFirstOnLoad.current = true;
    setOffset(nextOffset);
  };
}
