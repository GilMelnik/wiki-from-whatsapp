import { Dialog } from "./Dialog.jsx";
import { listShortcuts } from "./useShortcuts.js";

function comboLabel(keys) {
  return [].concat(keys).join(" / ");
}

/** The "?" dialog: every labelled shortcut that is bound on the page right now. */
export function ShortcutHelp({ open, onClose }) {
  const shortcuts = open ? listShortcuts() : [];
  return (
    <Dialog open={open} title="Keyboard shortcuts" onClose={onClose}>
      {shortcuts.length === 0 ? (
        <p className="text-slate-500">This tool has no shortcuts.</p>
      ) : (
        <table className="w-full">
          <tbody className="divide-y divide-slate-100">
            {shortcuts.map((shortcut) => (
              <tr key={`${comboLabel(shortcut.keys)} ${shortcut.label}`}>
                <td className="py-1.5 pe-4">{shortcut.label}</td>
                <td className="py-1.5 text-end">
                  <kbd dir="ltr" className="rounded border border-slate-300 bg-slate-50 px-1.5 py-0.5 font-mono text-xs">
                    {comboLabel(shortcut.keys)}
                  </kbd>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="mt-3 text-xs text-slate-500">
        Letters work on the Hebrew keyboard layout too. While you type in a field, only Ctrl combinations work.
      </p>
    </Dialog>
  );
}
