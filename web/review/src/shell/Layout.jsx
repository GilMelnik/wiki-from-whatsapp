import { useRef, useState } from "react";
import { PANEL_WIDTHS } from "./config.js";
import { clampWidth, widthFromPointer } from "./panelWidths.js";
import { ResizeHandle } from "./ResizeHandle.jsx";
import { readStored, storageKey, writeStored } from "./storage.js";

// The frame every tool renders into: an optional toolbar, then list | main | side.
//
//   <Layout
//     toolId={tool.name}
//     toolbar={<Filters />}       // optional strip above the columns (filters, sort, counters)
//     list={<ItemList ... />}     // optional: what can be selected
//     main={<Detail ... />}       // the selected item
//     side={<Actions ... />}      // optional: what can be done with it
//     listWidth={{ initial: 360 }}  // optional overrides of PANEL_WIDTHS, also `sideWidth`
//   />
//
// The user drags the bars between the columns; the widths are remembered per tool.

function Column({ as: Element, width, className, children }) {
  return (
    <Element style={{ width }} className={`flex min-h-0 min-w-0 shrink-0 flex-col ${className}`}>
      {children}
    </Element>
  );
}

export function Layout({ toolId, toolbar, list, main, side, listWidth, sideWidth }) {
  const limits = {
    list: { ...PANEL_WIDTHS.list, ...listWidth },
    side: { ...PANEL_WIDTHS.side, ...sideWidth },
  };
  const key = storageKey(toolId, "layout");
  const containerRef = useRef(null);
  const [widths, setWidths] = useState(() => {
    const stored = readStored(key, {});
    return {
      list: clampWidth(stored.list ?? limits.list.initial, limits.list),
      side: clampWidth(stored.side ?? limits.side.initial, limits.side),
    };
  });
  // Updated synchronously while dragging: the pointerup that saves the widths can arrive
  // before React re-rendered with the last move.
  const widthsRef = useRef(widths);

  const resize = (column, edge) => (clientX) => {
    const container = containerRef.current;
    const rect = container.getBoundingClientRect();
    const rtl = getComputedStyle(container).direction === "rtl";
    const width = clampWidth(widthFromPointer({ edge, rtl, rect, clientX }), limits[column]);
    widthsRef.current = { ...widthsRef.current, [column]: width };
    setWidths(widthsRef.current);
  };
  const remember = () => writeStored(key, widthsRef.current);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {toolbar && (
        <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-slate-200 bg-white px-4 py-2 text-sm">
          {toolbar}
        </div>
      )}
      <div ref={containerRef} className="flex min-h-0 flex-1">
        {list && (
          <>
            <Column as="aside" width={widths.list} className="bg-white">
              {list}
            </Column>
            <ResizeHandle
              label="שינוי רוחב הרשימה"
              onResize={resize("list", "start")}
              onResizeEnd={remember}
            />
          </>
        )}
        <main className="flex min-h-0 min-w-0 flex-1 flex-col bg-slate-50">{main}</main>
        {side && (
          <>
            <ResizeHandle
              label="שינוי רוחב הפאנל הצדדי"
              onResize={resize("side", "end")}
              onResizeEnd={remember}
            />
            <Column as="aside" width={widths.side} className="bg-slate-50">
              {side}
            </Column>
          </>
        )}
      </div>
    </div>
  );
}
