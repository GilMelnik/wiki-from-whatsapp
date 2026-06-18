import { useCallback, useEffect, useRef } from "react";

export default function ResizeHandle({ onResize, onResizeEnd, label }) {
  const draggingRef = useRef(false);

  const stop = useCallback(() => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    document.body.classList.remove("panel-resize-active");
    onResizeEnd?.();
  }, [onResizeEnd]);

  useEffect(() => {
    const onMove = (event) => {
      if (!draggingRef.current) return;
      event.preventDefault();
      onResize(event);
    };
    const onUp = () => stop();

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [onResize, stop]);

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      title={label}
      className="panel-resize-handle shrink-0"
      onMouseDown={(event) => {
        event.preventDefault();
        draggingRef.current = true;
        document.body.classList.add("panel-resize-active");
      }}
    />
  );
}
