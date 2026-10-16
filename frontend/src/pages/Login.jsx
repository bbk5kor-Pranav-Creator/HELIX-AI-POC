import { useState } from 'react';
import {
  ArrowRight,
  ChevronUp,
  Cpu,
  Layers3,
  LockKeyhole,
  Mail,
  Network,
  ShieldCheck,
  Sparkles,
  UserRound,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { saveWorkspaceSession } from '../utils/auth';

function Login() {
  const navigate = useNavigate();

  const [role, setRole] = useState('Admin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loginError, setLoginError] = useState('');
  const [isLoggingIn, setIsLoggingIn] = useState(false);

  const handleLogin = async (event) => {
    event.preventDefault();

    setLoginError('');

    if (!email || !password) {
      setLoginError('Please enter your corporate email and password.');
      return;
    }

    setIsLoggingIn(true);

    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          email,
          password,
          role,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || 'Invalid credentials.');
      }

      if (data.role === 'Employee' || data.role === 'Admin') {
        saveWorkspaceSession({
          email: email.trim(),
          role: data.role,
          accessToken: data.access_token,
        });
      }

      if (data.role === 'Employee') {
        navigate('/employee');
        return;
      }

      if (data.role === 'Admin') {
        navigate('/admin');
        return;
      }

      throw new Error('Invalid workspace role.');
    } catch (error) {
      setLoginError(
        error.message || 'Unable to sign in. Please try again.'
      );
    } finally {
      setIsLoggingIn(false);
    }
  };

  return (
    <div className="login-page">
      {/* Header */}
      <header className="login-header">
        <div className="header-brand">
          <img src="/bosch-logo.png" alt="Bosch" className="bosch-logo-image" />

          <div className="header-divider"></div>

          <div className="platform-brand">
            <h1>Helix Agentic Platform</h1>
            <p>From operational realities to aligned SOPs, with AI</p>
          </div>
        </div>

        <div className="organization-pill">
          <span className="organization-dot"></span>
          Digital Enterprise Operations
        </div>
      </header>

      {/* Main */}
      <main className="login-main">
        <section className="hero-section">
          <div className="hero-badge">
            <Sparkles size={16} />
            BOSCH ENTERPRISE PROCESS INTELLIGENCE
          </div>

          <h2>
            Aligning Documented SOPs
            <br />
            with Live Operational Realities
          </h2>

          <p className="hero-description">
            An intelligent agentic platform designed for Bosch enterprise
            excellence. Ingest documented business procedures across 10
            corporate repositories, conduct interactive AI-guided employee
            discovery interviews, and systematically surface genuine
            operational deviations.
          </p>

          <div className="feature-cards">
            <FeatureCard
              icon={<Layers3 size={20} />}
              iconClass="blue"
              title="10 Ingestion Channels"
              description="Docupedia, SharePoint, Jira, Signavio, Outlook, Docs, Media & Links."
            />

            <FeatureCard
              icon={<Cpu size={20} />}
              iconClass="purple"
              title="AI Interview Studio"
              description="Interactive employee dialogue capturing real operational handoffs."
            />

            <FeatureCard
              icon={<Network size={20} />}
              iconClass="green"
              title="Process Variation Log"
              description="Submit modified workflows, SOP deltas, and automation adjustments."
            />
          </div>
        </section>

        {/* Login Card */}
        <section className="signin-card">
          <div className="signin-heading">
            <div>
              <h3>Corporate Sign In</h3>
              <p>
                Select your workspace role and enter your corporate account
                credentials.
              </p>
            </div>

            <span className="sso-badge">ENTERPRISE SSO</span>
          </div>

          <div className="role-section">
            <label>
              Workspace Role <span>*</span>
            </label>

            <div className="role-switch">
              <button
                type="button"
                className={role === 'Admin' ? 'active' : ''}
                onClick={() => {
                  setRole('Admin');
                  setLoginError('');
                }}
              >
                <ShieldCheck size={16} />
                Admin
              </button>

              <button
                type="button"
                className={role === 'Employee' ? 'active' : ''}
                onClick={() => {
                  setRole('Employee');
                  setLoginError('');
                }}
              >
                <UserRound size={16} />
                Employee
              </button>
            </div>
          </div>

          <form className="signin-form" onSubmit={handleLogin}>
            <div className="signin-field">
              <label htmlFor="corporate-email">
                Corporate Email <span>*</span>
              </label>

              <div className="signin-input">
                <Mail size={18} />

                <input
                  id="corporate-email"
                  type="email"
                  placeholder="corporate-id@bosch.com"
                  value={email}
                  onChange={(event) => {
                    setEmail(event.target.value);
                    setLoginError('');
                  }}
                />
              </div>
            </div>

            <div className="signin-field">
              <label htmlFor="corporate-password">
                Password <span>*</span>
              </label>

              <div className="signin-input">
                <LockKeyhole size={18} />

                <input
                  id="corporate-password"
                  type="password"
                  placeholder="••••••••••••"
                  value={password}
                  onChange={(event) => {
                    setPassword(event.target.value);
                    setLoginError('');
                  }}
                />
              </div>
            </div>

            {loginError && (
              <div className="signin-error">
                {loginError}
              </div>
            )}

            <button
              type="submit"
              className="signin-button"
              disabled={isLoggingIn}
            >
              <span>
                {isLoggingIn
                  ? 'Signing In...'
                  : `Sign In as ${role}`}
              </span>

              <ArrowRight size={19} />
            </button>
          </form>

          <div className="signin-footer">
            <span>Authorized Bosch access only</span>

            <span className="tls-label">
              <ShieldCheck size={15} />
              TLS 1.3
            </span>
          </div>
        </section>
      </main>
    </div>
  );
}

function FeatureCard({ icon, iconClass, title, description }) {
  return (
    <div className="feature-card">
      <div className={`feature-icon ${iconClass}`}>{icon}</div>

      <h4>{title}</h4>

      <p>{description}</p>

      <div className="feature-arrow">
        <ChevronUp size={14} />
      </div>
    </div>
  );
}

export default Login;