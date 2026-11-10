import { Dialog, formatCount } from "@shell";
import { api, groupPath } from "./api.js";
import { GroupPicker } from "./GroupPicker.jsx";
import { pickedIn } from "./groups.js";

// The one dialog that is open in the groups view (`dialog.kind`) and what its confirmation
// saves. `act(request, message)` saves and throws on failure (the dialog shows the error and
// stays open).

export function DialogHost({ dialog, group, picked, close, act }) {
  if (!dialog || !group) return null;
  const path = groupPath(group.group_id);
  const suggested = group.partners.map((pair) => (pair.a.group_id === group.group_id ? pair.b : pair.a));
  const chosen = pickedIn(picked, group.group_id);

  switch (dialog.kind) {
    case "merge":
      return (
        <GroupPicker
          title="מיזוג עם קבוצה אחרת"
          description="הטענות של שתי הקבוצות יהיו קבוצה אחת; נשמר המזהה של הקבוצה הוותיקה יותר."
          excludeId={group.group_id}
          suggested={suggested}
          confirmLabel="מיזוג"
          onClose={close}
          onConfirm={(otherId) => act(() => api.post("/groups/merge", { group_ids: [group.group_id, otherId] }), "הקבוצות מוזגו")}
        />
      );
    case "move":
      return (
        <GroupPicker
          title="העברת הטענה לקבוצה אחרת"
          description={group.size === 1 ? "זו הטענה היחידה בקבוצה: הקבוצה תתמזג לתוך הקבוצה שתבחרו." : "הטענה תעבור לקבוצה שתבחרו."}
          excludeId={group.group_id}
          suggested={suggested}
          confirmLabel="העברה"
          onClose={close}
          onConfirm={(targetId) => act(() => api.post(`${path}/move`, { claim_id: chosen[0], target_id: targetId }), "הטענה הועברה")}
        />
      );
    case "link":
      return (
        <GroupPicker
          title="הוספת קישור סתירה"
          description="שתי הקבוצות יסומנו כסותרות זו את זו (שלב 5 ישמור את הקישור)."
          excludeId={group.group_id}
          confirmLabel="קישור"
          onClose={close}
          onConfirm={(otherId) => act(() => api.post(`${path}/contradictions`, { group_id: otherId, linked: true }), "קישור הסתירה נוסף")}
        />
      );
    case "split":
      return (
        <Dialog
          open
          tone="danger"
          title="פיצול לקבוצה חדשה"
          confirmLabel="פיצול"
          onClose={close}
          onConfirm={() => act(() => api.post(`${path}/split`, { claim_ids: chosen }), "הקבוצה פוצלה")}
        >
          <p>
            {formatCount(chosen.length)} הטענות שנבחרו יעברו לקבוצה חדשה, ו-{formatCount(group.size - chosen.length)} יישארו כאן.
            השתיים לא יוצעו למיזוג שוב.
          </p>
        </Dialog>
      );
    default:
      return null;
  }
}
