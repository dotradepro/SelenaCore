import { useCallback, useEffect, useRef, useState } from 'react';

/** Hook used by every template-rendered widget to load its JSON payload.
 *
 *  Three refresh sources are layered:
 *    1. Initial fetch on mount.
 *    2. Polling — fires every `pollIntervalS` seconds while mounted.
 *    3. Event-driven — listens for `selena_event` postMessage events that
 *       the existing SyncManager broadcasts to iframes; we hijack the same
 *       message bus inside the parent SPA so DashboardV2 widgets stay
 *       reactive without a second WebSocket. Refetches whenever any of
 *       `events` matches the broadcasted `event_type`.
 *
 *  All three paths share a single in-flight latch so reconnects + bursts
 *  of EventBus chatter coalesce into one request.
 */
export interface UseWidgetDataResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
}

export interface UseWidgetDataOpts {
  /** Module name as it appears in `manifest.name` — used for the proxy URL. */
  module: string;
  /** Logical key declared in `data_endpoints` (e.g. "state"). */
  key: string;
  /** EventBus topics to subscribe to. Empty array disables event-driven refresh. */
  events?: string[];
  /** Polling fallback interval in seconds. 0 disables polling. */
  pollIntervalS?: number;
  /** Hard fetch timeout in ms. Defaults to 5s; the proxy itself caps at 800ms. */
  timeoutMs?: number;
}

export function useWidgetData<T = unknown>(
  opts: UseWidgetDataOpts,
): UseWidgetDataResult<T> {
  const { module, key, events, pollIntervalS, timeoutMs = 5000 } = opts;

  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const inflight = useRef<Promise<void> | null>(null);
  const aliveRef = useRef(true);

  const fetchOnce = useCallback(async () => {
    if (inflight.current) return inflight.current;
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    const req = (async () => {
      try {
        const r = await fetch(`/api/v1/modules/${module}/data/${key}`, {
          signal: controller.signal,
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const body = (await r.json()) as T;
        if (aliveRef.current) {
          setData(body);
          setError(null);
        }
      } catch (e: unknown) {
        if (aliveRef.current) {
          setError(e instanceof Error ? e.message : 'fetch failed');
        }
      } finally {
        window.clearTimeout(timer);
        if (aliveRef.current) setLoading(false);
        inflight.current = null;
      }
    })();
    inflight.current = req;
    return req;
  }, [module, key, timeoutMs]);

  // Initial fetch + cleanup latch.
  useEffect(() => {
    aliveRef.current = true;
    setLoading(true);
    fetchOnce();
    return () => {
      aliveRef.current = false;
    };
  }, [fetchOnce]);

  // Polling fallback.
  useEffect(() => {
    if (!pollIntervalS || pollIntervalS <= 0) return;
    const id = window.setInterval(fetchOnce, pollIntervalS * 1000);
    return () => window.clearInterval(id);
  }, [fetchOnce, pollIntervalS]);

  // Event-driven refresh — piggyback on SyncManager's selena_event fanout
  // (the same channel iframes use). Parent SPA already routes incoming
  // WebSocket events through this listener via `broadcastEventToIframes`,
  // so we don't need a second connection.
  useEffect(() => {
    if (!events || events.length === 0) return;
    const wanted = new Set(events);
    function onMsg(e: MessageEvent) {
      const ev = e.data;
      if (!ev || ev.type !== 'selena_event') return;
      if (typeof ev.event_type !== 'string') return;
      if (wanted.has(ev.event_type)) {
        fetchOnce();
      }
    }
    window.addEventListener('message', onMsg);
    return () => window.removeEventListener('message', onMsg);
  }, [events, fetchOnce]);

  return { data, loading, error, refetch: fetchOnce };
}
