import { createApi } from "@shell";

// The entity tool's server, under /api/entities (one client for the whole tool).
export const api = createApi("entities");

export const entityPath = (entityId) => `/entities/${encodeURIComponent(entityId)}`;
