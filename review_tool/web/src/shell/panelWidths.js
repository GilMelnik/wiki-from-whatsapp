// Geometry of the resizable columns (pure functions; Layout.jsx holds the state).

export function clampWidth(value, { min, max }) {
  return Math.round(Math.min(max, Math.max(min, value)));
}

/**
 * Width of a column while its handle is dragged to `clientX`. `edge` is the side of the
 * container the column is attached to in reading order: "start" (the list) or "end" (the
 * side panel). In RTL the start edge is the right one.
 */
export function widthFromPointer({ edge, rtl, rect, clientX }) {
  const attachedToLeft = (edge === "start") !== rtl;
  return attachedToLeft ? clientX - rect.left : rect.right - clientX;
}
