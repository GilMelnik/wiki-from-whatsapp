import { Bidi, useResource } from "@shell";
import { api } from "./api.js";
import { ORIGIN_LABELS, releaseNote } from "./groups.js";

// What a merge would do beyond joining the claims, asked from the server before it happens
// (GET /merge-check): groups that dispute each other (the merge stays possible: the owner's
// merge wins, and the link between them goes) and whether the merged group would be kept off
// the wiki or released by it.

const TONES = { info: "note-info", success: "note-success", warning: "note-warning" };

/** The server's answer for merging `ids` (null while any id is missing). */
export function useMergeCheck(ids, watch) {
  const ready = ids.length > 1 && ids.every(Boolean);
  return useResource(() => (ready ? api.get("/merge-check", { group_ids: ids }) : null), [ids.join("|")], { watch });
}

export function DisputeWarning({ disputes }) {
  if (!disputes?.length) return null;
  return (
    <div role="alert" className="note note-warning space-y-1">
      <p className="font-medium">These groups dispute each other.</p>
      {disputes.map((dispute) => (
        <p key={dispute.group_id} className="text-xs">
          {ORIGIN_LABELS[dispute.origin] ?? "Members disputed each other"}
          {dispute.linked === false && " (you removed the link, but the claims still dispute each other)"}: “
          <Bidi>{dispute.claim_text}</Bidi>”.
        </p>
      ))}
      <p className="text-xs">
        Merge only if they really say the same thing: your merge wins, and the wiki then shows one statement instead of
        both sides.
      </p>
    </div>
  );
}

export function ReleaseNote({ check, minSupporters }) {
  const note = releaseNote(check, minSupporters);
  if (!note) return null;
  return <p className={`note ${TONES[note.tone]}`}>{note.text}</p>;
}

/** Both notes for a merge of `ids`, asked from the server. */
export function MergeNotes({ ids, minSupporters, watch }) {
  const check = useMergeCheck(ids, watch);
  if (!check.data) return null;
  return (
    <div className="space-y-2">
      <DisputeWarning disputes={check.data.disputes} />
      <ReleaseNote check={check.data} minSupporters={minSupporters} />
    </div>
  );
}
