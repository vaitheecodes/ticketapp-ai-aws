import { api } from "../api";
import AuthForm from "./AuthForm";

export default function CustomerLogin() {
  return (
    <AuthForm
      title="Customer Login"
      role="customer"
      redirectTo="/customer"
      onSignup={api.customerSignup}
      onLogin={api.customerLogin}
    />
  );
}
