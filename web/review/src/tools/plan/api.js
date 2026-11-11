import { createApi } from "@shell";

// The plan tool's server, under /api/plan (one client for the whole tool). The dry run of an
// operation and the id check, which the operation dialogs ask while the owner types, are the
// shell's (`usePreview`, `useIdCheck`).

export const api = createApi("plan");

export const pagePath = (pageId) => `/pages/${encodeURIComponent(pageId)}`;
