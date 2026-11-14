import { Bidi } from "@shell";
import { removedText } from "./review.js";

/** One row of the list (its section says where it stands): the claim's text and what was removed. */
export function ClaimRow({ claim }) {
  return (
    <span className="flex flex-col gap-0.5">
      <Bidi as="span" className="line-clamp-2 text-slate-800">
        {claim.text}
      </Bidi>
      <span className="text-xs text-slate-500">{removedText(claim.redaction_counts)}</span>
    </span>
  );
}
