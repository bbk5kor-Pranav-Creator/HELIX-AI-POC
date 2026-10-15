import {
  ArrowLeftRight,
  ArrowUpRight,
  Grid2X2,
  MessageSquare,
  Network,
  Sparkles,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import EmployeeProfileMenu from '../components/common/EmployeeProfileMenu';
import './employee.css';

const studioCards = [
  {
    title: 'AI Interview Studio',
    tag: 'Interactive AI',
    description:
      'Conduct an AI-powered contextual interview based on business-process changes and operational realities.',
    action: 'Launch Interview Studio',
    icon: MessageSquare,
    type: 'blue',
  },
  {
    title: 'Process Understanding',
    tag: 'Learning Platform',
    description:
      'Interactive learning platform to master enterprise business processes tailored to your process chain and role.',
    action: 'Explore Processes',
    icon: Network,
    type: 'purple',
  },
  {
    title: 'Knowledge Transfer',
    tag: 'Knowledge Handover',
    description:
      'Build structured operational knowledge when transitioning into a new role or inheriting responsibilities from another employee.',
    action: 'Access Knowledge',
    icon: ArrowLeftRight,
    type: 'green',
  },
];

function EmployeeDashboard() {
  const navigate = useNavigate();

  const handleStudioCardClick = (title) => {

    if (title === 'AI Interview Studio') {
      navigate('/employee/studio/ai-interview');
    }
    if (title === 'Process Understanding') {
      navigate('/employee/studio/process-understanding');
    }

    if (title === 'Knowledge Transfer') {
      navigate('/employee/studio/knowledge-transfer');
    }
  };

  return (
    <div className="employee-app">
      <header className="login-header employee-topbar">
        <div className="header-brand topbar-brand">
          <img src="/bosch-logo.png" alt="Bosch" className="bosch-logo-image" />

          <div className="header-divider topbar-divider" />

          <div className="platform-brand">
            <h1>Helix Agentic Platform</h1>
            <p>From operational realities to aligned SOPs, with AI</p>
          </div>
        </div>

        <EmployeeProfileMenu
          placement="header"
          profileClassName="employee-profile"
        />
      </header>

      <div className="employee-body">
        <aside className="employee-sidebar">
          <nav>
            <button className="sidebar-item active" type="button">
              <Sparkles size={17} />
              Studio
            </button>

            <button className="sidebar-item" type="button"
                onClick={() => navigate('/employee/dashboard')} >

              <Grid2X2 size={17} />
              Dashboard
            </button>
          </nav>

          <EmployeeProfileMenu
            placement="sidebar"
            wrapperClassName="sidebar-bottom"
            profileClassName="sidebar-user"
          />
        </aside>

        <main className="employee-main">
          <div className="studio-heading">
            <div className="studio-label">
              <Sparkles size={15} />
              ENTERPRISE WORKSPACE
            </div>

            <h2>Studio</h2>

            <p>
              AI-powered capabilities for process learning, contextual
              interviews, and structured knowledge transfer across Bosch
              operational workflows.
            </p>
          </div>

          <section className="studio-cards">
            {studioCards.map((card) => {
              const Icon = card.icon;

              return (
                <article
                  className="studio-card"
                  key={card.title}
                  onClick={() => handleStudioCardClick(card.title)}
                  role={
                    card.title === 'Process Understanding' ||
                    card.title === 'Knowledge Transfer'
                      ? 'button'
                      : undefined
                  }
                  tabIndex={
                    card.title === 'Process Understanding' ||
                    card.title === 'Knowledge Transfer'
                      ? 0
                      : undefined
                  }
                  onKeyDown={(event) => {
                    if (
                      event.key === 'Enter' ||
                      event.key === ' '
                    ) {
                      handleStudioCardClick(card.title);
                    }
                  }}
                >
                  <div className="card-top">
                    <div className={`card-icon ${card.type}`}>
                      <Icon size={20} />
                    </div>

                    <span className="card-tag">{card.tag}</span>
                  </div>

                  <h3>{card.title}</h3>

                  <p>{card.description}</p>

                  <div className="card-action">
                    <strong>{card.action}</strong>

                    <span>
                      <ArrowUpRight size={15} />
                    </span>
                  </div>
                </article>
              );
            })}
          </section>

          <section className="intelligence-panel">
            <div className="intelligence-title">
              <Grid2X2 size={17} />
              <strong>BOSCH ENTERPRISE PROCESS INTELLIGENCE</strong>
            </div>

            <p>
              Helix AI bridges corporate standard operating procedures with
              actual shopfloor, engineering, and administrative realities.
              Select a capability tile above to engage in an AI-assisted
              interview session, explore role-specific process models, or
              initiate a knowledge handover.
            </p>

            <div className="intelligence-status">
              <span>✓ SOP Alignment Connected</span>
              <span>✓ Role-Specific Discovery</span>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}

export default EmployeeDashboard;