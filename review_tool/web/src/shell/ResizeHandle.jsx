import { useRef } from "react";

/** The draggable bar between two columns. `onResize(clientX)` while dragging. */
export function ResizeHandle({ label, onResize, onResizeEnd }) {
  const dragging = useRef(false);

  const stop = (event) => {
    if (!dragging.current) return;
    dragging.current = false;
    event.currentTarget.releasePointerCapture(event.pointerId);
    document.body.classList.remove("panel-resize-active");
    onResizeEnd();
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      title={label}
      className="panel-resize-handle shrink-0"
      onPointerDown={(event) => {
        event.preventDefault();
        dragging.current = true;
        event.currentTarget.setPointerCapture(event.pointerId);
        document.body.classList.add("panel-resize-active");
      }}
      onPointerMove={(event) => {
        if (dragging.current) onResize(event.clientX);
      }}
      onPointerUp={stop}
      onPointerCancel={stop}
    />
  );
}
