import { useEffect, useState } from "react";
import { Kbd, Select } from "@shell";
import { knowledgeFilterOptions, MODES, progressText, SORTS, windowLabel } from "./threads.js";

// The strip above the columns: what is listed (every conversation, searchable and filtered by
// the knowledge label once step 2 has run — also to the conversations that need a label and to
// those the owner's edits made or changed; or the gold windows, one at a time, with their done
// marks).

const SEARCH_DELAY_MS = 300;

function SearchBox({ value, onChange }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  useEffect(() => {
    if (text === value) return undefined;
    const timer = setTimeout(() => onChange(text), SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
  }, [text]);
  return (
    <input
      id="threads-search"
      type="search"
      dir="auto"
      className="field w-64"
      placeholder="Search messages and senders"
      value={text}
      onChange={(event) => setText(event.target.value)}
    />
  );
}

function WindowControls({ data, locked, onToggleDone }) {
  const { windows, windowIndex, selectWindow } = data;
  const current = windows[windowIndex];
  return (
    <>
      <Select
        label="Window"
        value={String(windowIndex)}
        options={windows.map((window) => ({ value: String(window.index), label: windowLabel(window) }))}
        onChange={(value) => selectWindow(Number(value))}
      />
      <button type="button" className="btn btn-sm" disabled={windowIndex === 0} onClick={() => selectWindow(windowIndex - 1)}>
        Previous <Kbd>[</Kbd>
      </button>
      <button type="button" className="btn btn-sm" disabled={windowIndex >= windows.length - 1} onClick={() => selectWindow(windowIndex + 1)}>
        Next <Kbd>]</Kbd>
      </button>
      <button
        type="button"
        className={`btn btn-sm ${current.done ? "" : "btn-primary"}`}
        disabled={locked}
        onClick={onToggleDone}
        title={current.done ? "The window is marked done; click to reopen it" : "Every message in the window is in its right conversation"}
      >
        {current.done ? "Done ✓ — reopen" : "Mark window done"} <Kbd>d</Kbd>
      </button>
      <span className="text-xs text-slate-500">{progressText(windows)}</span>
    </>
  );
}

export function Toolbar({ data, locked, onToggleDone }) {
  const modes = [
    { value: MODES.all, label: "All conversations" },
    ...(data.windows.length ? [{ value: MODES.gold, label: "Gold windows" }] : []),
  ];
  return (
    <>
      {modes.length > 1 && <Select label="Show" value={data.gold ? MODES.gold : MODES.all} options={modes} onChange={data.setMode} />}
      {data.gold ? (
        <WindowControls data={data} locked={locked} onToggleDone={onToggleDone} />
      ) : (
        <>
          <SearchBox value={data.search.q} onChange={(q) => data.setSearch((previous) => ({ ...previous, q }))} />
          <Select label="Order" value={data.search.sort} options={SORTS} onChange={(sort) => data.setSearch((previous) => ({ ...previous, sort }))} />
          {data.knowledge.data?.ready && (
            <Select
              label="Knowledge"
              value={data.search.knowledge ?? "all"}
              options={knowledgeFilterOptions(data.knowledge.data)}
              onChange={(knowledge) => data.setSearch((previous) => ({ ...previous, knowledge }))}
            />
          )}
        </>
      )}
    </>
  );
}
