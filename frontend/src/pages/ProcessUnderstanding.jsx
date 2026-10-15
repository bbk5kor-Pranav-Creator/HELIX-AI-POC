import {
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  Grid2X2,
  Network,
  Sparkles,
} from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import EmployeeProfileMenu from '../components/common/EmployeeProfileMenu';
import './employee.css';
import './process-understanding.css';

const processChains = ['S2C', 'I2P-IP', 'R2B', 'PPM', 'M2S'];

const roles = [
  'Process Associate',
  'Key User',
  'Functional Consultant',
  'Technical Consultant',
  'Process Expert / SME',
  'Process Owner',
];

function ProcessUnderstanding() {
    const navigate = useNavigate();
  const [processChain, setProcessChain] = useState('');
  const [role, setRole] = useState('');

  const handleProcessChainChange = (event) => {
    setProcessChain(event.target.value);
  };

  const handleRoleChange = (event) => {
    setRole(event.target.value);
  };

  const canGainKnowledge = processChain !== '' && role !== '';

  return (
    <div className="employee-app process-page">
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
            <button
              className="sidebar-item active"
              type="button"
              onClick={() => navigate('/employee')}
            >
              <Sparkles size={17} />
              Studio
            </button>

            <button
              className="sidebar-item"
              type="button"
              onClick={() => navigate('/employee/dashboard')}
            >
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

        <main className="employee-main process-content">
          <button
            className="back-button"
            type="button"
            onClick={() => navigate('/employee')}
          >
            <ArrowLeft size={16} />
            Back to Studio
          </button>

          <div className="process-heading">
            <div className="process-eyebrow">
              <Sparkles size={15} />
              PROCESS UNDERSTANDING
            </div>

            <h1>Gain Process Knowledge</h1>

            <p>
              Select a process chain and role to gain a role-specific
              understanding of the relevant business process.
            </p>
          </div>

        <section className="selection-card">
          <div className="selection-field">
            <label htmlFor="process-chain">Process Chain</label>

            <div className="select-wrapper">
              <Network size={18} />

              <select
                id="process-chain"
                value={processChain}
                onChange={handleProcessChainChange}
              >
                <option value="">Select Process Chain</option>

                {processChains.map((chain) => (
                  <option key={chain} value={chain}>
                    {chain}
                  </option>
                ))}
              </select>

              <ChevronDown size={16} />
            </div>
          </div>

          <div className="selection-field">
            <label htmlFor="role">Role</label>

            <div className="select-wrapper">
              <select
                id="role"
                value={role}
                onChange={handleRoleChange}
              >
                <option value="">Select Role</option>

                {roles.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>

              <ChevronDown size={16} />
            </div>
          </div>
        </section>

        {canGainKnowledge ? (
          <section className="process-overview">
            <div>
              <span>PROCESS OVERVIEW</span>

              <h2>{processChain}</h2>

              <p>
                <strong>Role:</strong> {role}
              </p>

              <div className="overview-description">
                Your process knowledge will be generated based on the selected
                process chain and role.
              </div>
            </div>

            <button
              className="gain-knowledge-button"
              type="button"
            >
              Gain Knowledge
              <ArrowRight size={17} />
            </button>
          </section>
        ) : (
          <div className="knowledge-placeholder">
            Select a Process Chain and Role to continue.
          </div>
        )}
        </main>
      </div>
    </div>
  );
}

export default ProcessUnderstanding;