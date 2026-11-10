import { useEffect, useState } from "react";
import { createApi, useLatest } from "@shell";
import { ID_CHECK_DEBOUNCE_MS, PREVIEW_DEBOUNCE_MS } from "./plan.js";

// The plan tool's server, under /api/plan (one client for the whole tool), and the two
// "ask while the owner types" flows: the dry run of an operation and the id check.

export const api = createApi("plan");

export const pagePath = (pageId) => `/pages/${encodeURIComponent(pageId)}`;

/**
 * Dry run of `op` (null = the form is not complete yet), repeated whenever it changes:
 * `{ preview: {label, edit} | null, error: ApiError | null, pending }`. The server runs the
 * real operation on its current plan and answers what it would do, or why it cannot.
 */
export function usePreview(op) {
  const [state, setState] = useState({ preview: null, error: null, pending: false });
  const begin = useLatest();
  const key = op ? JSON.stringify(op) : null;
  useEffect(() => {
    const isCurrent = begin();
    if (!key) {
      setState({ preview: null, error: null, pending: false });
      return undefined;
    }
    setState((previous) => ({ ...previous, pending: true }));
    const timer = setTimeout(() => {
      api
        .post("/operations/preview", JSON.parse(key))
        .then((preview) => isCurrent() && setState({ preview, error: null, pending: false }))
        .catch((error) => isCurrent() && setState({ preview: null, error, pending: false }));
    }, PREVIEW_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [key, begin]);
  return state;
}

/** Whether `id` can be given to a new item: `{ok, problem}` (null while unknown). */
export function useIdCheck(id) {
  const [answer, setAnswer] = useState(null);
  const begin = useLatest();
  useEffect(() => {
    const isCurrent = begin();
    setAnswer(null);
    if (!id) return undefined;
    const timer = setTimeout(() => {
      api
        .get("/ids", { id })
        .then((result) => isCurrent() && setAnswer(result))
        .catch((error) => isCurrent() && setAnswer({ ok: false, problem: error.message }));
    }, ID_CHECK_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [id, begin]);
  return answer;
}
