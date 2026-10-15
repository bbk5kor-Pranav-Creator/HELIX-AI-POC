import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  FileText,
  Grid2X2,
  MessageSquare,
  Send,
  Sparkles,
} from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import EmployeeProfileMenu from '../components/common/EmployeeProfileMenu';
import './employee.css';
import './ai-interview-studio.css';

function AIInterviewStudio() {
  const navigate = useNavigate();
  const [interviewStarted, setInterviewStarted] = useState(false);
  const [response, setResponse] = useState('');

  const startInterview = () => {
    setInterviewStarted(true);
  };

  return (
    <div className="employee-app interview-page">
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

        <main className="employee-main interview-content">
        <button
          className="interview-back-button"
          type="button"
          onClick={() => navigate('/employee')}
        >
          <ArrowLeft size={16} />
          Back to Studio
        </button>

        <div className="interview-heading">
          <div className="interview-eyebrow">
            <Sparkles size={15} />
            INTERACTIVE AI INTERVIEW
          </div>

          <h1>AI Interview Studio</h1>

          <p>
            Capture the difference between documented business processes and
            the way work is actually performed in day-to-day operations.
          </p>
        </div>

        {!interviewStarted ? (
          <section className="interview-intro-card">
            <div className="intro-icon">
              <MessageSquare size={21} />
            </div>

            <div className="intro-content">
              <span className="intro-label">HOW THIS INTERVIEW WORKS</span>

              <h2>Understand the reality behind the process</h2>

              <p className="intro-description">
                Helix uses documented SOPs and submitted change logs as the
                context for a structured employee interview.
              </p>

              <div className="interview-principles">
                <div className="interview-principle">
                  <div className="principle-icon">
                    <FileText size={17} />
                  </div>

                  <div>
                    <strong>Helix reads documented SOPs and change logs</strong>

                    <span>
                      Official process documentation and submitted changes
                      provide the interview context.
                    </span>
                  </div>
                </div>

                <div className="interview-principle">
                  <div className="principle-icon">
                    <MessageSquare size={17} />
                  </div>

                  <div>
                    <strong>Questions are posed sequentially</strong>

                    <span>
                      The interview progresses step-by-step to understand how
                      activities are actually performed.
                    </span>
                  </div>
                </div>

                <div className="interview-principle">
                  <div className="principle-icon">
                    <CheckCircle2 size={17} />
                  </div>

                  <div>
                    <strong>Responses capture daily workarounds</strong>

                    <span>
                      Employee responses help corporate teams identify
                      deviations and align official procedures with reality.
                    </span>
                  </div>
                </div>
              </div>
            </div>

            <div className="interview-context">
              <div className="context-header">
                <span>INTERVIEW CONTEXT</span>
              </div>

              <div className="context-row">
                <span>Process Context</span>
                <strong>Ready for AI context</strong>
              </div>

              <div className="context-row">
                <span>SOP Documentation</span>
                <strong className="context-ready">
                  <CheckCircle2 size={14} />
                  Available
                </strong>
              </div>

              <div className="context-row">
                <span>Change Logs</span>
                <strong className="context-ready">
                  <CheckCircle2 size={14} />
                  Available
                </strong>
              </div>

              <p>
                Actual SOP and change-log availability will be connected
                through the backend.
              </p>
            </div>

            <button
              className="start-interview-button"
              type="button"
              onClick={startInterview}
            >
              Start Interview
              <ArrowRight size={16} />
            </button>
          </section>
        ) : (
          <section className="interview-workspace">
            <div className="workspace-header">
              <div className="workspace-title">
                <div className="workspace-icon">
                  <MessageSquare size={19} />
                </div>

                <div>
                  <span>AI INTERVIEW SESSION</span>
                  <h2>Process Reality Interview</h2>
                </div>
              </div>

              <div className="workspace-status">
                <span />
                Interview Ready
              </div>
            </div>

            <div className="workspace-context">
              <FileText size={15} />

              <span>
                Interview context will be generated from the employee's
                applicable SOPs and submitted change logs.
              </span>
            </div>

            <div className="conversation-area">
              <div className="conversation-placeholder">
                <div className="placeholder-icon">
                  <Sparkles size={22} />
                </div>

                <h3>AI interviewer is ready</h3>

                <p>
                  The first contextual question will be generated here once
                  the enterprise AI service and FastAPI backend are connected.
                </p>
              </div>
            </div>

            <div className="response-area">
              <textarea
                value={response}
                onChange={(event) => setResponse(event.target.value)}
                placeholder="Your response will be entered here..."
                disabled
              />

              <button
                className="send-response-button"
                type="button"
                disabled
              >
                <Send size={16} />
                Submit Response
              </button>
            </div>
          </section>
        )}
        </main>
      </div>
    </div>
  );
}

export default AIInterviewStudio;