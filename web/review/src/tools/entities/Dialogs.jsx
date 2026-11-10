import { Bidi, Dialog, Ltr } from "@shell";
import { api, entityPath } from "./api.js";
import { CreateGroupDialog, GroupDialog } from "./ClaimGroupDialogs.jsx";
import { DeletedNamesDialog } from "./DeletedNamesDialog.jsx";
import { TargetDialog } from "./TargetDialog.jsx";

// The one dialog that is open (`dialog.kind`), and what its confirmation saves. `act(request,
// message)` saves and throws on failure (the dialog shows the error and stays open);
// `fire(request, message)` saves and reports a failure as a toast.

const ENTITY_FREE = new Set(["deleteClaim", "createGroup", "group", "deletedNames"]);

function Danger({ title, confirmLabel, onClose, onConfirm, children }) {
  return (
    <Dialog open tone="danger" title={title} confirmLabel={confirmLabel} onClose={onClose} onConfirm={onConfirm}>
      {children}
    </Dialog>
  );
}

export function DialogHost({ dialog, entity, view, saving, reads, close, act, fire }) {
  if (!dialog) return null;
  if (!entity && !ENTITY_FREE.has(dialog.kind)) return null; // the entity went away meanwhile
  const path = entity ? entityPath(entity.entity_id) : null;
  const target = (targetId) => ({ target_entity_id: targetId });

  switch (dialog.kind) {
    case "merge":
      return (
        <TargetDialog
          title={<>מיזוג &quot;<Bidi>{entity.canonical_name}</Bidi>&quot; לתוך ישות אחרת</>}
          description="כל השמות והטענות יעברו לישות שתבחרו, והישות הזאת תיעלם."
          allowNew={false}
          excludeId={entity.entity_id}
          suggested={entity.conflicts.filter((conflict) => conflict.entity_id)}
          initialTarget={dialog.targetId}
          confirmLabel="מיזוג"
          onClose={close}
          onConfirm={(targetId) => act(() => api.post(`${path}/merge`, target(targetId)), "הישות מוזגה")}
        />
      );
    case "moveMember":
      return (
        <TargetDialog
          title={<>העברת השם &quot;<Bidi>{dialog.name}</Bidi>&quot;</>}
          description="השם עובר עם כל הטענות שלו. בחרו ישות קיימת או ישות חדשה."
          allowNew
          excludeId={entity.entity_id}
          confirmLabel="העברה"
          onClose={close}
          onConfirm={(targetId) =>
            act(() => api.post(`${path}/members/move`, { name: dialog.name, ...target(targetId) }), "השם הועבר")
          }
        />
      );
    case "claims":
      return (
        <TargetDialog
          title={`${dialog.mode === "copy" ? "העתקת" : "העברת"} ${dialog.claimIds.length} טענות של "${dialog.name}"`}
          description={dialog.mode === "copy" ? "הטענות יישארו גם כאן." : "הטענות יוסרו מהשם הזה בישות הזאת."}
          allowNew
          excludeId={entity.entity_id}
          confirmLabel={dialog.mode === "copy" ? "העתקה" : "העברה"}
          onClose={close}
          onConfirm={(targetId) =>
            act(
              () => api.post(`${path}/claims/${dialog.mode}`, { name: dialog.name, claim_ids: dialog.claimIds, ...target(targetId) }),
              dialog.mode === "copy" ? "הטענות הועתקו" : "הטענות הועברו"
            )
          }
        />
      );
    case "reject":
      return (
        <Danger
          title="דחיית האיחוד"
          confirmLabel="פירוק הישות"
          onClose={close}
          onConfirm={() => act(() => api.post(`${path}/reject`), "האיחוד נדחה")}
        >
          <p>
            כל שם של &quot;<Bidi>{entity.canonical_name}</Bidi>&quot; יהפוך לישות מוצעת נפרדת, רק עם
            הטענות שמסומנות בשם הזה (טענות שרק מזכירות אותו בטקסט יוסרו ממנו).
          </p>
        </Danger>
      );
    case "remove":
      return (
        <Danger
          title="מחיקת הישות"
          confirmLabel="מחיקה"
          onClose={close}
          onConfirm={() => act(() => api.del(path, { params: view }), "הישות נמחקה")}
        >
          <p>
            &quot;<Bidi>{entity.canonical_name}</Bidi>&quot; תימחק. שמות שלה שאינם בישות אחרת יירשמו
            כשמות שנמחקו ושלב 4 לא יחזיר אותם; אפשר לשחזר אותם מ„שמות שנמחקו”.
          </p>
        </Danger>
      );
    case "deleteClaim":
      return (
        <Danger
          title="מחיקת הטענה"
          confirmLabel="מחיקת הטענה"
          onClose={close}
          onConfirm={() => act(() => api.del(`/claims/${encodeURIComponent(dialog.claim.claim_id)}`), "הטענה נמחקה")}
        >
          <Bidi as="p" className="mb-2 rounded bg-slate-50 p-2">
            {dialog.claim.claim_text}
          </Bidi>
          <p>
            הטענה <Ltr>{dialog.claim.claim_id}</Ltr> תוסר מכל הישויות ומהקבוצות, ושלב 5 ידלג עליה.
          </p>
        </Danger>
      );
    case "createGroup":
      return (
        <CreateGroupDialog
          claimIds={dialog.claimIds}
          texts={dialog.texts}
          onClose={close}
          onConfirm={(representative) =>
            act(() => api.post("/aggregations", { claim_ids: dialog.claimIds, representative }), "הטענות אוחדו")
          }
        />
      );
    case "group": {
      const groupPath = `/aggregations/${encodeURIComponent(dialog.group.id)}`;
      const then = (request, message) => fire(request, message).then(close);
      return (
        <GroupDialog
          group={dialog.group}
          saving={saving}
          onClose={close}
          onRepresentative={(claimId) => then(() => api.post(`${groupPath}/representative`, { claim_id: claimId }), "הטענה המייצגת הוחלפה")}
          onDecouple={(claimId) => then(() => api.post(`${groupPath}/decouple`, { claim_id: claimId }), "הטענה הוצאה מהקבוצה")}
          onDissolve={() => then(() => api.del(groupPath), "הקבוצה פורקה")}
        />
      );
    }
    case "deletedNames":
      return (
        <DeletedNamesDialog
          reads={reads}
          saving={saving}
          onClose={close}
          onRestore={(name) => fire(() => api.post("/deleted-names/restore", { name }), "השם שוחזר כישות חדשה").then(close)}
        />
      );
    default:
      return null;
  }
}
