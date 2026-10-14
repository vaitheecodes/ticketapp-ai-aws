import { Link } from "react-router-dom";
import { HeadsetIcon, SettingsIcon, TicketIcon, UserIcon } from "../components/Icons";

export default function Landing() {
  return (
    <div className="page centered">
      <div className="hero-brand">
        <TicketIcon className="hero-brand-icon" />
        <h1 className="hero-title">Ticket Resolution Agent</h1>
      </div>
      <p className="hero-subtitle">Choose a portal to sign in.</p>
      <div className="portal-cards">
        <Link className="portal-card" to="/customer/login">
          <UserIcon className="portal-card-icon" />
          <span className="portal-card-title">Customer Portal</span>
          <span className="portal-card-desc">Raise tickets, track responses</span>
        </Link>
        <Link className="portal-card" to="/agent/login">
          <HeadsetIcon className="portal-card-icon" />
          <span className="portal-card-title">Agent Portal</span>
          <span className="portal-card-desc">Work the escalation queue</span>
        </Link>
        <Link className="portal-card" to="/admin/login">
          <SettingsIcon className="portal-card-icon" />
          <span className="portal-card-title">Admin Portal</span>
          <span className="portal-card-desc">All tickets &amp; routing policy</span>
        </Link>
      </div>
    </div>
  );
}
