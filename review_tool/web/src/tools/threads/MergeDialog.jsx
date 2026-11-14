import { useEffect, useState } from "react";
import { Bidi, countText, Dialog, formatSpan, Ltr, ThreadTag } from "@shell";

// Merge the opened conversation with another: one of those in view (the one the chat had
// right before it is offered first), or any other by its id. The merged conversation keeps
// the id of the one that began first.

export function MergeDialog({ open, currentId, candidates, colors, onClose, onMerge }) {
  const [choice, setChoice] = useState("");
  const [typed, setTyped] = useState("");
  useEffect(() => {
    if (open) {
      setChoice(candidates[0]?.thread_id ?? "");
      setTyped("");
    }
  }, [open]);
  const target = typed.trim() || choice;
  return (
    <Dialog
      open={open}
      title="Merge conversations"
      confirmLabel="Merge"
      confirmDisabled={!target}
      onClose={onClose}
      onConfirm={() => onMerge([currentId, target])}
      size="lg"
    >
      <div className="space-y-3">
        <p className="flex items-center gap-1.5">
          Merge <ThreadTag info={colors.peek(currentId)} threadId={currentId} /> <Ltr>{currentId}</Ltr> with:
        </p>
        <div className="max-h-72 space-y-1.5 overflow-y-auto">
          {candidates.map((block) => (
            <label key={block.thread_id} className="flex cursor-pointer items-start gap-2 rounded-md border border-slate-200 p-2 hover:bg-slate-50">
              <input type="radio" name="merge-target" className="mt-1" checked={!typed && choice === block.thread_id} onChange={() => { setChoice(block.thread_id); setTyped(""); }} />
              <span className="min-w-0 flex-1 space-y-0.5">
                <span className="flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
                  <ThreadTag info={colors.peek(block.thread_id)} threadId={block.thread_id} /> <Ltr>{block.thread_id}</Ltr>
                  {formatSpan(block.start_time, block.last_time)} · {countText(block.size, "message")}
                </span>
                <Bidi as="span" className="line-clamp-2 block text-sm">{block.messages[0]?.content ?? ""}</Bidi>
              </span>
            </label>
          ))}
        </div>
        <label className="flex items-center gap-2">
          Or another, by id
          <input dir="ltr" className="field flex-1 font-mono" value={typed} placeholder="t-…" onChange={(event) => setTyped(event.target.value)} />
        </label>
      </div>
    </Dialog>
  );
}
