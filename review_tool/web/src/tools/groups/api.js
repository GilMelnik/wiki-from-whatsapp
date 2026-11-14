import { createApi } from "@shell";

// The groups tool's server, under /api/groups (one client for the whole tool).
export const api = createApi("groups");

export const groupPath = (groupId) => `/groups/${encodeURIComponent(groupId)}`;
