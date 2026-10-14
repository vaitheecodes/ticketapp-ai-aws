import { api } from "../api";
import AuthForm from "./AuthForm";

export default function AgentLogin() {
  return (
    <AuthForm
      title="Agent Login"
      role="agent"
      redirectTo="/agent"
      onSignup={api.agentSignup}
      onLogin={api.agentLogin}
    />
  );
}
