import { useEffect, useState } from 'react';

/** Period of the day used to drive the hero gradient tint.
 *  Boundaries match docs/dashboard-recraft.md §2.2:
 *    night    22:00–06:00 — cool blue
 *    morning  06:00–10:00 — warm amber
 *    day      10:00–18:00 — neutral white-blue
 *    evening  18:00–22:00 — golden
 */
export type TimeOfDay = 'night' | 'morning' | 'day' | 'evening';

export function timeOfDayForHour(hour: number): TimeOfDay {
  if (hour >= 22 || hour < 6) return 'night';
  if (hour < 10) return 'morning';
  if (hour < 18) return 'day';
  return 'evening';
}

/** Sets `<html data-tod="night|morning|day|evening">` and refreshes every
 *  15 minutes so `--hero-tint` (defined in index.css per data-tod) stays
 *  in sync with local time. Returns the current value for components that
 *  also want to render time-aware copy. */
export function useTimeOfDay(): TimeOfDay {
  const [tod, setTod] = useState<TimeOfDay>(() => timeOfDayForHour(new Date().getHours()));

  useEffect(() => {
    const apply = () => {
      const next = timeOfDayForHour(new Date().getHours());
      document.documentElement.dataset.tod = next;
      setTod(next);
    };
    apply();
    const id = window.setInterval(apply, 15 * 60 * 1000);
    return () => window.clearInterval(id);
  }, []);

  return tod;
}
