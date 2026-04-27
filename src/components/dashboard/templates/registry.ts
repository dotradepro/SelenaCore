import type { ComponentType } from 'react';
import type { Module } from '../../../store/useStore';
import MetricTemplate from './Metric';
import ToggleListTemplate from './ToggleList';
import SparklineTemplate from './Sparkline';
import ControlPanelTemplate from './ControlPanel';
import StatusTemplate from './Status';

export interface TemplateProps {
  mod: Module;
}

/** Registry mapping `manifest.ui.widget.template` to its React renderer.
 *  Phase 3 finishes the built-in set: metric, toggle-list, sparkline,
 *  control-panel, status. Returns `null` for unknown names so
 *  `WidgetFrame` can fall back to the iframe path. */
export type TemplateName = 'metric' | 'sparkline' | 'toggle-list' | 'control-panel' | 'status';

const REGISTRY: Partial<Record<TemplateName, ComponentType<TemplateProps>>> = {
  'metric': MetricTemplate,
  'toggle-list': ToggleListTemplate,
  'sparkline': SparklineTemplate,
  'control-panel': ControlPanelTemplate,
  'status': StatusTemplate,
};

export function getTemplate(name: string | undefined): ComponentType<TemplateProps> | null {
  if (!name) return null;
  return REGISTRY[name as TemplateName] ?? null;
}
