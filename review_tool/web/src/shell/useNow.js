import { useEffect, useState } from "react";
import { CLOCK_TICK_MS } from "./config.js";

/** The current time in ms, refreshed every `CLOCK_TICK_MS`, for relative times. */
export function useNow() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), CLOCK_TICK_MS);
    return () => clearInterval(timer);
  }, []);
  return now;
}
