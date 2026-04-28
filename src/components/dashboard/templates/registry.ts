import type { ComponentType } from 'react';
import type { Module } from '../../../store/useStore';
import MetricTemplate from './Metric';
import ToggleListTemplate from './ToggleList';
import SparklineTemplate from './Sparkline';
import ControlPanelTemplate from './ControlPanel';
import StatusTemplate from './Status';
import WeatherTemplate from './Weather';
import MediaTemplate from './Media';
import PresenceTemplate from './Presence';

export interface TemplateProps {
  mod: Module;
}

/** Registry mapping ``manifest.ui.widget.template`` → React renderer.
 *  Phase 3 set: metric, toggle-list, sparkline, control-panel, status.
 *  Phase 6 specialized: weather, media, presence.
 *  Returns ``null`` for unknown names so ``WidgetFrame`` can fall back
 *  to the iframe path for ``kind: "custom"`` modules. */
export type TemplateName =
  | 'metric'
  | 'sparkline'
  | 'toggle-list'
  | 'control-panel'
  | 'status'
  | 'weather'
  | 'media'
  | 'presence';

const REGISTRY: Partial<Record<TemplateName, ComponentType<TemplateProps>>> = {
  'metric': MetricTemplate,
  'toggle-list': ToggleListTemplate,
  'sparkline': SparklineTemplate,
  'control-panel': ControlPanelTemplate,
  'status': StatusTemplate,
  'weather': WeatherTemplate,
  'media': MediaTemplate,
  'presence': PresenceTemplate,
};

export function getTemplate(name: string | undefined): ComponentType<TemplateProps> | null {
  if (!name) return null;
  return REGISTRY[name as TemplateName] ?? null;
}
