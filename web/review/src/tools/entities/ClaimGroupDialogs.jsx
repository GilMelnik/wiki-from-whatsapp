import { useState } from "react";
import { Bidi, Dialog, Ltr } from "@shell";

// Manual claim groups (near-duplicates step 5 merges under one representative): creating one
// from the picked claims, and looking at / changing an existing one.

export function CreateGroupDialog({ claimIds, texts, onClose, onConfirm }) {
  const [representative, setRepresentative] = useState(claimIds[0]);
  return (
    <Dialog
      open
      size="lg"
      title={`איחוד ${claimIds.length} טענות`}
      confirmLabel="איחוד"
      onClose={onClose}
      onConfirm={() => onConfirm(representative)}
    >
      <p className="mb-3 text-slate-600">
        בחרו את הטענה שתייצג את הקבוצה. השאר יוסתרו כאן, ושלב 5 יאחד אותן איתה.
      </p>
      <ul className="space-y-1">
        {claimIds.map((claimId) => (
          <li key={claimId}>
            <label className="flex cursor-pointer items-start gap-2 rounded bg-slate-50 p-2">
              <input
                type="radio"
                name="group-representative"
                className="mt-1"
                checked={representative === claimId}
                onChange={() => setRepresentative(claimId)}
              />
              <span className="min-w-0 flex-1">
                <Bidi as="span" className="block">
                  {texts[claimId] || claimId}
                </Bidi>
                <Ltr className="text-xs text-slate-400">{claimId}</Ltr>
              </span>
            </label>
          </li>
        ))}
      </ul>
    </Dialog>
  );
}

export function GroupDialog({ group, saving, onClose, onRepresentative, onDecouple, onDissolve }) {
  return (
    <Dialog open size="lg" title={`טענות מאוחדות (${group.count})`} onClose={onClose}>
      <ul className="space-y-1">
        {group.members.map((member) => (
          <li key={member.claim_id} className="flex items-start gap-2 rounded bg-slate-50 p-2">
            <span className="min-w-0 flex-1">
              <Bidi as="span" className="block">
                {member.claim_text || member.claim_id}
              </Bidi>
              <Ltr className="text-xs text-slate-400">{member.claim_id}</Ltr>
            </span>
            {member.claim_id === group.representative ? (
              <span className="badge badge-sky shrink-0">מייצגת</span>
            ) : (
              <button type="button" className="btn btn-sm shrink-0" disabled={saving} onClick={() => onRepresentative(member.claim_id)}>
                קביעה כמייצגת
              </button>
            )}
            <button type="button" className="btn btn-sm shrink-0" disabled={saving} onClick={() => onDecouple(member.claim_id)}>
              הוצאה מהקבוצה
            </button>
          </li>
        ))}
      </ul>
      <button type="button" className="btn btn-sm mt-3 text-rose-700" disabled={saving} onClick={onDissolve}>
        פירוק הקבוצה
      </button>
    </Dialog>
  );
}
