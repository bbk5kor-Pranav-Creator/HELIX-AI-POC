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
import { getEmployeeSession } from '../utils/auth';
import './employee.css';
import './ai-interview-studio.css';

function buildInterviewQuestions(chunks) {
  const seen = new Set();
  const statements = chunks.flatMap((chunk) => {
    const sentences = chunk.content
      .split(/(?<=[.!?])\s+|\r?\n+/)
      .map((sentence) => sentence.trim())
      .filter(Boolean);
    return (sentences.length ? sentences : [chunk.content.trim()])
      .map((sentence) => ({
        title: chunk.title,
        statement: sentence.length > 240
          ? `${sentence.slice(0, 237).trimEnd()}...`
          : sentence,
      }));
  });

  return statements
    .filter(({ statement }) => {
      const key = statement.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 5)
    .map(({ title, statement }, index) => ({
      id: `${index}-${statement}`,
      title,
      prompt: `The source states: “${statement}” How does this step work in your day-to-day process?`,
    }));
}

function AIInterviewStudio() {
  const navigate = useNavigate();
  const [interviewStarted, setInterviewStarted] = useState(false);
  const [response, setResponse] = useState('');
  const [isLoadingContext, setIsLoadingContext] = useState(false);
  const [contextError, setContextError] = useState('');
  const [interviewContext, setInterviewContext] = useState(null);

  const startInterview = async () => {
    setInterviewStarted(true);
    setIsLoadingContext(true);
    setContextError('');
    setInterviewContext(null);
    const session = getEmployeeSession();

    if (!session?.accessToken) {
      setContextError('Your Employee session is missing or expired. Please sign in again.');
      setIsLoadingContext(false);
      return;
    }

    try {
      const result = await fetch('/api/knowledge/interview-context', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${session.accessToken}`,
        },
        body: JSON.stringify({
          query: 'process steps responsibilities roles handoffs decisions exceptions controls',
        }),
      });
      const data = await result.json();
      if (!result.ok) {
        throw new Error(data.detail || 'Unable to retrieve interview context.');
      }

      setInterviewContext({
        ...data,
        questions: buildInterviewQuestions(data.chunks),
      });
    } catch (error) {
      setContextError(error.message || 'Unable to retrieve interview context.');
    } finally {
      setIsLoadingContext(false);
    }
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
                Helix retrieves process text saved by an Admin and uses those
                source statements to prepare a structured employee interview.
              </p>

              <div className="interview-principles">
                <div className="interview-principle">
                  <div className="principle-icon">
                    <FileText size={17} />
                  </div>

                  <div>
                    <strong>Questions use saved process text</strong>

                    <span>
                      Only text retrieved from the shared knowledge repository
                      provides interview context.
                    </span>
                  </div>
                </div>

                <div className="interview-principle">
                  <div className="principle-icon">
                    <MessageSquare size={17} />
                  </div>

                  <div>
                    <strong>Questions are grounded in source statements</strong>

                    <span>
                      Each prompt quotes text returned by the backend and asks
                      how the described step is performed in practice.
                    </span>
                  </div>
                </div>

                <div className="interview-principle">
                  <div className="principle-icon">
                    <CheckCircle2 size={17} />
                  </div>

                  <div>
                    <strong>Questions invite operational context</strong>

                    <span>
                      Use the prompts to reflect on the process statements
                      available from saved sources.
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
                <span>Knowledge Sources</span>
                <strong>{interviewContext?.sources.length ?? 'Loaded when interview starts'}</strong>
              </div>

              <div className="context-row">
                <span>Retrieved Text Chunks</span>
                <strong>{interviewContext?.chunks.length ?? 'Not requested'}</strong>
              </div>

              <p>
                Interview questions are grounded in text saved by an Admin.
              </p>
            </div>

            <button
              className="start-interview-button"
              type="button"
              onClick={startInterview}
              disabled={isLoadingContext}
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
                {isLoadingContext ? 'Loading context' : contextError ? 'Context unavailable' : 'Interview Ready'}
              </div>
            </div>

            <div className="workspace-context">
              <FileText size={15} />

              <span>
                {interviewContext
                  ? `Retrieved ${interviewContext.chunks.length} text chunk${interviewContext.chunks.length === 1 ? '' : 's'} from ${interviewContext.sources.map((source) => source.title).join(', ')}.`
                  : 'Questions are generated only from text retrieved from saved knowledge sources.'}
              </span>
            </div>

            <div className="conversation-area">
              {isLoadingContext ? (
                <div className="conversation-placeholder" role="status">
                  <div className="placeholder-icon">
                    <Sparkles size={22} />
                  </div>
                  <h3>Retrieving interview context...</h3>
                  <p>Searching text sources saved by an Admin.</p>
                </div>
              ) : contextError ? (
                <div className="conversation-placeholder" role="alert">
                  <div className="placeholder-icon">
                    <FileText size={22} />
                  </div>
                  <h3>{contextError}</h3>
                  <p>Ask an Admin to save relevant process text before starting an interview.</p>
                </div>
              ) : interviewContext?.questions.length ? (
                interviewContext.questions.map((question, index) => (
                  <div className="conversation-placeholder" key={question.id}>
                    <div className="placeholder-icon">
                      <MessageSquare size={22} />
                    </div>
                    <h3>Interview question {index + 1}</h3>
                    <p>{question.prompt}</p>
                    <p>Source: {question.title}</p>
                  </div>
                ))
              ) : (
                <div className="conversation-placeholder">
                  <div className="placeholder-icon">
                    <Sparkles size={22} />
                  </div>
                  <h3>No interview questions are available</h3>
                  <p>No retrieved source statements were available to form questions.</p>
                </div>
              )}
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