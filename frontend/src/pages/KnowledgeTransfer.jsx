import {
  ArrowLeft,
  BriefcaseBusiness,
  ChevronDown,
  Sparkles,
} from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import EmployeeProfileMenu from '../components/common/EmployeeProfileMenu';
import './employee.css';
import './knowledge-transfer.css';

const handoverScenarios = [
  'Succession / Replacing Predecessor',
  'Role Transition',
  'Process Ownership Transition',
];

const processChains = ['S2C', 'I2P-IP', 'R2B', 'PPM', 'M2S'];

const predecessorRoles = [
  'Fresher',
  'New Joinee',
  'Apprentice',
  'Process Associate',
  'Key User',
  'Functional Consultant',
  'Technical Consultant',
  'Process Expert / SME',
  'Process Owner',
];

function KnowledgeTransfer() {
  const navigate = useNavigate();

  const [handoverScenario, setHandoverScenario] = useState(
    'Succession / Replacing Predecessor'
  );
  const [processChain, setProcessChain] = useState('S2C');
  const [predecessorRole, setPredecessorRole] = useState('Fresher');

  return (
    <div className="knowledge-transfer-page">
      <header className="login-header knowledge-transfer-header">
        <div className="header-brand knowledge-transfer-brand">
          <img src="/bosch-logo.png" alt="Bosch" className="bosch-logo-image" />

          <div className="header-divider knowledge-transfer-divider" />

          <div className="platform-brand">
            <h1>Helix Agentic Platform</h1>
            <p>From operational realities to aligned SOPs, with AI</p>
          </div>
        </div>

        <EmployeeProfileMenu
          placement="header"
          profileClassName="knowledge-transfer-profile"
        />
      </header>

      <div className="knowledge-transfer-body">
        <aside className="knowledge-transfer-sidebar">
          <nav>
            <button
              className="knowledge-sidebar-item active"
              type="button"
            >
              <Sparkles size={17} />
              Studio
            </button>

            <button
              className="knowledge-sidebar-item"
              type="button"
            >
              <div className="dashboard-grid-icon">
                <span />
                <span />
                <span />
                <span />
              </div>
              Dashboard
            </button>
          </nav>

          <EmployeeProfileMenu
            placement="sidebar"
            wrapperClassName="knowledge-sidebar-bottom"
            profileClassName="knowledge-sidebar-user"
            logoutClassName="knowledge-logout"
          />
        </aside>

        <main className="knowledge-transfer-main">
          <button
            className="knowledge-back-button"
            type="button"
            onClick={() => navigate('/employee')}
          >
            <ArrowLeft size={16} />
            Back to Studio
          </button>

          <div className="knowledge-heading">
            <div className="knowledge-eyebrow">
              <Sparkles size={15} />
              OPERATIONAL HANDOVER &amp; TRANSITION PLATFORM
            </div>

            <h2>Knowledge Transfer</h2>

            <p>
              Structured operational transition suite when taking over
              responsibilities from a predecessor or adapting an enterprise
              workflow.
            </p>
          </div>

          <section className="handover-card">
            <div className="handover-card-header">
              <div className="handover-step">1</div>

              <strong>CONFIGURE HANDOVER PARAMETERS</strong>
            </div>

            <div className="handover-fields">
              <div className="handover-field">
                <label htmlFor="handover-scenario">
                  Handover Scenario
                </label>

                <div className="handover-select">
                  <select
                    id="handover-scenario"
                    value={handoverScenario}
                    onChange={(event) =>
                      setHandoverScenario(event.target.value)
                    }
                  >
                    {handoverScenarios.map((scenario) => (
                      <option key={scenario} value={scenario}>
                        {scenario}
                      </option>
                    ))}
                  </select>

                  <ChevronDown size={16} />
                </div>
              </div>

              <div className="handover-field">
                <label htmlFor="target-process-chain">
                  Target Process Chain / Domain
                </label>

                <div className="handover-select">
                  <select
                    id="target-process-chain"
                    value={processChain}
                    onChange={(event) =>
                      setProcessChain(event.target.value)
                    }
                  >
                    {processChains.map((chain) => (
                      <option key={chain} value={chain}>
                        {chain}
                      </option>
                    ))}
                  </select>

                  <ChevronDown size={16} />
                </div>
              </div>

              <div className="handover-field">
                <label htmlFor="predecessor-role">
                  Predecessor Role / Designation
                </label>

                <div className="handover-select">
                  <select
                    id="predecessor-role"
                    value={predecessorRole}
                    onChange={(event) =>
                      setPredecessorRole(event.target.value)
                    }
                  >
                    {predecessorRoles.map((role) => (
                      <option key={role} value={role}>
                        {role}
                      </option>
                    ))}
                  </select>

                  <ChevronDown size={16} />
                </div>
              </div>
            </div>

            <div className="handover-footer">
              <p>
                Generates actionable transition checklists, critical system
                authorizations, recurring duties, and workaround audits.
              </p>

              <button
                className="generate-dossier-button"
                type="button"
              >
                <BriefcaseBusiness size={16} />
                Generate Handover Dossier
              </button>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}

export default KnowledgeTransfer;