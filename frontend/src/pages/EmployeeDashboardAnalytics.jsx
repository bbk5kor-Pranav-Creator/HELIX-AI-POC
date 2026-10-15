import {
  ArrowUpRight,
  BriefcaseBusiness,
  CheckCircle2,
  FileText,
  Grid2X2,
  MessageSquare,
  Network,
  Sparkles,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import EmployeeProfileMenu from '../components/common/EmployeeProfileMenu';
import './employee.css';
import './employee-dashboard-analytics.css';

const metrics = [
  {
    title: 'AI INTERVIEWS',
    value: '0',
    description: 'No active sessions',
    action: 'Open Studio',
    icon: MessageSquare,
    type: 'blue',
  },
  {
    title: 'PROCESS CHANGES',
    value: '0',
    description: '0 modifications logged',
    action: 'View Changes',
    icon: FileText,
    type: 'purple',
  },
  {
    title: 'UPLOADED DOCUMENTS',
    value: '0',
    description: '0 files attached',
    action: 'Manage Files',
    icon: FileText,
    type: 'green',
  },
  {
    title: 'LEARNING SESSIONS',
    value: '0',
    description: '0 completed sessions',
    action: 'Start Learning',
    icon: Network,
    type: 'pink',
  },
];

function EmployeeDashboardAnalytics() {
  const navigate = useNavigate();

  return (
    <div className="analytics-page">
      <header className="login-header analytics-header">
        <div className="header-brand analytics-brand">
          <img src="/bosch-logo.png" alt="Bosch" className="bosch-logo-image" />

          <div className="header-divider analytics-header-divider" />

          <div className="platform-brand">
            <h1>Helix Agentic Platform</h1>
            <p>From operational realities to aligned SOPs, with AI</p>
          </div>
        </div>

        <EmployeeProfileMenu
          placement="header"
          profileClassName="analytics-profile"
        />
      </header>

      <div className="analytics-body">
        <aside className="analytics-sidebar">
          <nav>
            <button
              className="analytics-sidebar-item"
              type="button"
              onClick={() => navigate('/employee')}
            >
              <Sparkles size={17} />
              Studio
            </button>

            <button
              className="analytics-sidebar-item active"
              type="button"
            >
              <Grid2X2 size={17} />
              Dashboard
            </button>
          </nav>

          <EmployeeProfileMenu
            placement="sidebar"
            wrapperClassName="analytics-sidebar-bottom"
            profileClassName="analytics-sidebar-user"
            logoutClassName="analytics-logout"
          />
        </aside>

        <main className="analytics-main">
          <div className="analytics-heading-row">
            <div className="analytics-heading">
              <div className="analytics-eyebrow">
                <Sparkles size={14} />
                WORKSPACE TELEMETRY
              </div>

              <h2>Employee Dashboard</h2>

              <p>
                Activity and engagement telemetry for Process Employee.
              </p>
            </div>

            <div className="session-status">
              <span />
              Session Active
            </div>
          </div>

          <section className="metrics-grid">
            {metrics.map((metric) => {
              const Icon = metric.icon;

              return (
                <article className="metric-card" key={metric.title}>
                  <div className="metric-card-top">
                    <span>{metric.title}</span>

                    <div className={`metric-icon ${metric.type}`}>
                      <Icon size={16} />
                    </div>
                  </div>

                  <strong className="metric-value">{metric.value}</strong>

                  <p>{metric.description}</p>

                  <div className="metric-divider" />

                  <button
                    className="metric-action"
                    type="button"
                    onClick={() => {
                      if (metric.action === 'Open Studio') {
                        navigate('/employee');
                      }

                      if (metric.action === 'Start Learning') {
                        navigate(
                          '/employee/studio/process-understanding'
                        );
                      }
                    }}
                  >
                    {metric.action}
                    <ArrowUpRight size={14} />
                  </button>
                </article>
              );
            })}
          </section>

          <section className="analytics-content-grid">
            <div className="dashboard-panel changes-panel">
              <div className="panel-header">
                <div>
                  <h3>PROCESS CHANGES DISTRIBUTION</h3>
                  <p>
                    Modifications categorized by operational adjustment type
                  </p>
                </div>

                <span className="total-badge">0 Total</span>
              </div>

              <div className="empty-chart">
                <div className="empty-chart-icon">
                  <FileText size={22} />
                </div>

                <strong>No process changes recorded yet</strong>

                <p>
                  Submit procedural variations, automation scripts, or
                  workarounds to view classification analytics.
                </p>

                <button type="button">
                  + Submit Process Change
                </button>
              </div>
            </div>

            <div className="dashboard-panel capabilities-panel">
              <div className="panel-header">
                <div>
                  <h3>STUDIO CAPABILITIES ACTIVITY</h3>
                </div>
              </div>

              <div className="capability-list">
                <article className="capability-card process-capability">
                  <div className="capability-top">
                    <div className="capability-title">
                      <Network size={15} />
                      <strong>Process Understanding</strong>
                    </div>

                    <span>0 sessions</span>
                  </div>

                  <p>
                    Explore role-based enterprise process chains.
                  </p>

                  <button
                    type="button"
                    onClick={() =>
                      navigate(
                        '/employee/studio/process-understanding'
                      )
                    }
                  >
                    Explore Process Chains →
                  </button>
                </article>

                <article className="capability-card knowledge-capability">
                  <div className="capability-top">
                    <div className="capability-title">
                      <BriefcaseBusiness size={15} />
                      <strong>Knowledge Transfer</strong>
                    </div>

                    <span>0 handovers</span>
                  </div>

                  <p>
                    Gain operational knowledge when inheriting or
                    transitioning responsibilities.
                  </p>

                  <button
                    type="button"
                    onClick={() =>
                      navigate(
                        '/employee/studio/knowledge-transfer'
                      )
                    }
                  >
                    Access Handover Platform →
                  </button>
                </article>
              </div>

              <div className="telemetry-note">
                <CheckCircle2 size={14} />

                <span>
                  Telemetry connects directly with local session state
                  and synchronizes with the enterprise backend.
                </span>
              </div>
            </div>
          </section>

          <section className="dashboard-panel interview-participation">
            <div className="panel-header">
              <div>
                <h3>AI INTERVIEW PARTICIPATION</h3>
              </div>

              <span className="participation-status">Not Started</span>
            </div>

            <div className="participation-empty">
              <div className="participation-icon">
                <MessageSquare size={19} />
              </div>

              <div>
                <strong>No interview sessions yet</strong>

                <p>
                  Start an AI Interview Studio session to capture
                  operational realities and daily workarounds.
                </p>
              </div>

              <button
                type="button"
                onClick={() => navigate('/employee/studio/ai-interview')}
              >
                Open Interview Studio
                <ArrowUpRight size={14} />
              </button>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}

export default EmployeeDashboardAnalytics;