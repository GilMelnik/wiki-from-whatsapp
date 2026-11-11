import { useEffect, useState } from "react";
import { createApi } from "./api.js";
import { useLatest } from "./latest.js";
import { ID_CHECK_DEBOUNCE_MS, PREVIEW_DEBOUNCE_MS } from "./planText.js";
import { useResource } from "./useResource.js";

// The plan tool's server (under /api/plan) as every tool that edits a plan operation asks it,
// while the owner types: the dry run of an operation, whether a new id is free, and the plan
// overview the operation dialogs are built on. The plan tool owns these endpoints; the
// operation dialogs (PlanDialogs.jsx) call them through here.

const planApi = createApi("plan");
const PLAN_KEYS = ["plan"];

/**
 * Dry run of `op` (null = the form is not complete yet), repeated whenever it changes:
 * `{ preview: {label, edit, assignments} | null, error: ApiError | null, pending }`. The server
 * runs the real operation on its current plan and answers what it would do, or why it cannot.
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
      planApi
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
      planApi
        .get("/ids", { id })
        .then((result) => isCurrent() && setAnswer(result))
        .catch((error) => isCurrent() && setAnswer({ ok: false, problem: error.message }));
    }, ID_CHECK_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [id, begin]);
  return answer;
}

/** The plan as GET /api/plan/plan answers it (nodes, pages, labels, label kinds, ...), kept
 * current when the plan changes on disk: what the operation dialogs take as `overview`. */
export function usePlanOverview() {
  return useResource(() => planApi.get("/plan"), [], { watch: PLAN_KEYS });
}
