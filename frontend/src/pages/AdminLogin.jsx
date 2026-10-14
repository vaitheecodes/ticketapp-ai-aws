import { api } from "../api";
import AuthForm from "./AuthForm";

export default function AdminLogin() {
  return (
    <AuthForm
      title="Admin Login"
      role="admin"
      redirectTo="/admin"
      onSignup={api.adminSignup}
      onLogin={api.adminLogin}
    />
  );
}
