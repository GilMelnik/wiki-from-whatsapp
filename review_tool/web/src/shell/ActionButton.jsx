import { formatCount } from "./format.js";
import { Kbd } from "./text.jsx";

// A button of a "Decide" / "Actions" side column: the action on the left, its key on the
// right. `count` (optional) says how many items it acts on and disables it at 0; `primary`
// marks the decision the screen is for, `danger` one that removes something.
//
//   <ActionButton label="Accept" keyName="a" primary onClick={accept} />

export function ActionButton({ label, keyName, count, primary = false, danger = false, disabled = false, title, onClick }) {
  const tone = primary ? "btn-primary" : danger ? "text-rose-700" : "";
  return (
    <button type="button" title={title} className={`btn btn-block justify-between ${tone}`} disabled={disabled || count === 0} onClick={onClick}>
      <span className="text-start">
        {label}
        {count !== undefined && count !== null && ` (${formatCount(count)})`}
      </span>
      {keyName && <Kbd>{keyName}</Kbd>}
    </button>
  );
}
