import DashboardV2 from './dashboard/DashboardV2';

/** Entry point for the dashboard route. Phase 5 retired the V1 fixed
 *  5×4 carousel; the file remains as a thin shim so the existing routing
 *  in App.tsx (`<Route path="/" element={<Dashboard />} />`) keeps working
 *  without an import-site change. New code should target ``DashboardV2``
 *  directly. */
export default function Dashboard() {
  return <DashboardV2 />;
}
