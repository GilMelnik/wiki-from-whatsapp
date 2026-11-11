import { createApi } from "@shell";

// The suggestions tool's server, under /api/suggestions (one client for the whole tool).

export const api = createApi("suggestions");

export const suggestionPath = (id) => `/suggestions/${encodeURIComponent(id)}`;
