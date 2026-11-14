import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

// The banner slot under the header: a standing notice about the state of the page, shown
// for as long as its condition holds (lost connection, missing input files, and later
// "an input changed, re-run the step").
//
//   useBanner("pii.unsaved", dirty ? { tone: "warning", text: "Some changes are not saved" } : null);
//   useBanner("stale", stale ? { tone: "warning", text: "...", actionLabel: "Run again",
//                               onAction: rerun } : null);
//
// The hook is declarative: pass the banner while it applies and `null` when it does not.
// Ids are page-wide, so prefix them with the tool id. Tones: info, warning, danger, success.

const SetBannerContext = createContext(null);
const BannerListContext = createContext([]);

const TONES = {
  info: "border-sky-200 bg-sky-50 text-sky-900",
  warning: "border-amber-200 bg-amber-50 text-amber-900",
  danger: "border-rose-200 bg-rose-50 text-rose-900",
  success: "border-emerald-200 bg-emerald-50 text-emerald-900",
};

export function BannerProvider({ children }) {
  const [banners, setBanners] = useState([]);
  const setBanner = useCallback((id, banner) => {
    setBanners((list) => {
      const others = list.filter((item) => item.id !== id);
      return banner === null ? others : [...others, { ...banner, id }];
    });
  }, []);
  return (
    <SetBannerContext.Provider value={setBanner}>
      <BannerListContext.Provider value={banners}>{children}</BannerListContext.Provider>
    </SetBannerContext.Provider>
  );
}

export function useBanner(id, banner) {
  const setBanner = useContext(SetBannerContext);
  if (setBanner === null) throw new Error("useBanner must be used inside the review shell");
  const onActionRef = useRef(null);
  onActionRef.current = banner?.onAction ?? null;
  const text = banner?.text ?? null;
  const tone = banner?.tone ?? "info";
  const actionLabel = banner?.actionLabel ?? null;

  useEffect(() => {
    if (text === null) return undefined;
    setBanner(id, { tone, text, actionLabel, onAction: () => onActionRef.current?.() });
    return () => setBanner(id, null);
  }, [setBanner, id, tone, text, actionLabel]);
}

/** Rendered once by the shell, under the header. */
export function BannerSlot() {
  const banners = useContext(BannerListContext);
  if (banners.length === 0) return null;
  return (
    <div className="shrink-0">
      {banners.map((banner) => (
        <div
          key={banner.id}
          role="status"
          className={`flex items-center gap-3 border-b px-4 py-1.5 text-sm ${TONES[banner.tone]}`}
        >
          <span className="min-w-0 flex-1">{banner.text}</span>
          {banner.actionLabel && (
            <button type="button" className="btn btn-sm shrink-0" onClick={banner.onAction}>
              {banner.actionLabel}
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
