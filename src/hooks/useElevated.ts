import { useCallback, useState } from 'react';
import { useStore } from '../store/useStore';

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

    const isElevated = Boolean(elevatedToken);

    /**
     * Execute `action` immediately if an elevated token already exists,
     * otherwise open the PIN modal and run `action` after successful PIN entry.
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
