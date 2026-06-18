const STORAGE_KEY = "plan_reviewer_panel_layout";

export const DEFAULT_LIST_WIDTH = 280;
export const DEFAULT_SIDE_WIDTH = 340;
export const MIN_LIST_WIDTH = 220;
export const MAX_LIST_WIDTH = 480;
export const MIN_SIDE_WIDTH = 280;
export const MAX_SIDE_WIDTH = 560;

export function clampListWidth(value) {
  return Math.round(Math.min(MAX_LIST_WIDTH, Math.max(MIN_LIST_WIDTH, value)));
}

export function clampSideWidth(value) {
  return Math.round(Math.min(MAX_SIDE_WIDTH, Math.max(MIN_SIDE_WIDTH, value)));
}

export function loadPanelLayout() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return {
        listWidth: DEFAULT_LIST_WIDTH,
        sideWidth: DEFAULT_SIDE_WIDTH,
      };
    }
    const data = JSON.parse(raw);
    return {
      listWidth: clampListWidth(data.listWidth ?? DEFAULT_LIST_WIDTH),
      sideWidth: clampSideWidth(data.sideWidth ?? DEFAULT_SIDE_WIDTH),
    };
  } catch {
    return {
      listWidth: DEFAULT_LIST_WIDTH,
      sideWidth: DEFAULT_SIDE_WIDTH,
    };
  }
}

export function savePanelLayout(layout) {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      listWidth: clampListWidth(layout.listWidth),
      sideWidth: clampSideWidth(layout.sideWidth),
    })
  );
}
