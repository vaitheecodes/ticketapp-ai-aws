import RoutingPolicyPanel from "../components/RoutingPolicyPanel";

export default function DebugPanel() {
  return (
    <div className="page">
      <h1>Routing Policy (dev/debug only)</h1>
      <RoutingPolicyPanel />
    </div>
  );
}
