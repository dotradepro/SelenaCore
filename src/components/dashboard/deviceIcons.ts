/** Frontend mirror of the backend `core/api/widget_helpers.ENTITY_ICON`
 *  table. Used by `DeviceDetailModal` and any UI that renders a device
 *  outside the toggle-list payload (where the backend pre-resolves the
 *  icon for us). Keep in sync with the Python table when adding new
 *  entity types. */

const ENTITY_ICON: Record<string, string> = {
  light: 'lightbulb',
  switch: 'power',
  outlet: 'zap',
  fan: 'wind',
  ac: 'thermometer',
  climate: 'thermometer',
  thermostat: 'thermometer',
  lock: 'shield',
  camera: 'eye',
  tv: 'tv',
  speaker: 'volume-2',
  media_player: 'music',
  sensor: 'activity',
};

/** Return the lucide-style icon name for a device entity type, or
 *  ``undefined`` if the type is unknown (the `Icon` component then
 *  renders the supplied fallback). Case-insensitive. */
export function entityIconForFrontend(entityType: string | null | undefined): string | undefined {
  if (!entityType) return undefined;
  return ENTITY_ICON[entityType.toLowerCase()];
}
