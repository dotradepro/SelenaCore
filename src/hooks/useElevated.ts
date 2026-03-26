import { useCallback, useRef, useState } from 'react';
import { useStore } from '../store/useStore';

/**
 * Monkey-patch global fetch so that any request carrying X-Elevated-Token
 * that receives a 403 automatically clears the stale elevated token from
 * the store and sessionStorage.  This lets `requestElevation` re-prompt
 * the PIN modal on the next attempt instead of silently failing.
 */
const _origFetch = window.fetch;
let _patchApplied = false;
function ensureFetchPatch() {
    if (_patchApplied) return;
    _patchApplied = true;
    window.fetch = async function patchedFetch(input, init) {
        const res = await _origFetch.call(window, input, init);
        if (
            res.status === 403 &&
            init?.headers &&
            typeof init.headers === 'object' &&
            'X-Elevated-Token' in (init.headers as Record<string, string>)
        ) {
            try { sessionStorage.removeItem('selena_elevated'); } catch { /* */ }
            useStore.getState().setElevatedToken(null);
        }
        return res;
    };
}

/**
 * Hook for requesting elevated (PIN-confirmed) authorization.
 *
 * Usage:
 *   const { isElevated, requestElevation, elevatedToken } = useElevated();
 *
 *   // To gate a sensitive action:
 *   const handleDelete = () =>
 *     requestElevation(() => { ... deleteStuff() ... });
 *
 * The hook internally tracks whether a PIN modal should be shown.
 * Wire <PinConfirmModal isOpen={showPin} onClose={closePin} onSuccess={onElevated} />
 * via the returned helpers.
 */
export function useElevated() {
    const elevatedToken = useStore((s) => s.elevatedToken);
    const setElevatedToken = useStore((s) => s.setElevatedToken);

    const [pinOpen, setPinOpen] = useState(false);
    const [pendingAction, setPendingAction] = useState<(() => void) | null>(null);
    const retriedRef = useRef(false);

    const isElevated = Boolean(elevatedToken);

    ensureFetchPatch();

    /**
     * Execute `action` immediately if an elevated token already exists,
     * otherwise open the PIN modal and run `action` after successful PIN entry.
     *
     * If the action triggers a 403 (stale token), the fetch patch clears the
     * elevated token automatically.  On the next click the user will be
     * prompted for their PIN again.
     */
    const requestElevation = useCallback(
        (action: () => void) => {
            if (elevatedToken) {
                action();
            } else {
                // Store the action and open PIN modal
                setPendingAction(() => action);
                setPinOpen(true);
            }
        },
        [elevatedToken],
    );

    /** Called by PinConfirmModal when PIN was accepted */
    const onElevated = useCallback(
        (token: string) => {
            setElevatedToken(token);
            setPinOpen(false);
            if (pendingAction) {
                pendingAction();
                setPendingAction(null);
            }
        },
        [pendingAction, setElevatedToken],
    );

    const closePin = useCallback(() => {
        setPinOpen(false);
        setPendingAction(null);
    }, []);

    return {
        isElevated,
        elevatedToken,
        requestElevation,
        /** Pass these directly into <PinConfirmModal /> */
        pinModalProps: {
            isOpen: pinOpen,
            onClose: closePin,
            onSuccess: onElevated,
        },
    };
}
