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
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import EmployeeProfileMenu from '../components/common/EmployeeProfileMenu';
import {
  createInterviewQuestion,
  createInterviewSession,
  completeSession,
  getInterviewSession,
  submitAnswer,
} from '../services/interview';
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
        source_id: chunk.source_id,
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
    .map(({ source_id, title, statement }, index) => ({
      id: `${index}-${statement}`,
      source_id,
      title,
      prompt: `The source states: “${statement}” How does this step work in your day-to-day process?`,
    }));
}

function hydrateQuestion(question) {
  const sources = question.knowledge_sources || [];
  return {
    id: question.id,
    title: sources.map((source) => source.source_title_snapshot).join(', ')
      || 'Knowledge source',
    prompt: question.question_text,
    source_id: sources[0]?.knowledge_source_id_snapshot,
    answers: question.answers || [],
  };
}

function AIInterviewStudio() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const sessionIdFromUrl = searchParams.get('session_id');
  const [interviewStarted, setInterviewStarted] = useState(false);
  const [response, setResponse] = useState('');
  const [isLoadingContext, setIsLoadingContext] = useState(false);
  const [contextError, setContextError] = useState('');
  const [completionError, setCompletionError] = useState('');
  const [interviewContext, setInterviewContext] = useState(null);
  const [interviewSessionId, setInterviewSessionId] = useState(sessionIdFromUrl);
  const [sessionStatus, setSessionStatus] = useState(null);
  const [selectedQuestionId, setSelectedQuestionId] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isCompleting, setIsCompleting] = useState(false);
  const [submissionError, setSubmissionError] = useState('');
  const startingSession = useRef(false);
  const submittingAnswer = useRef(false);
  const completingSession = useRef(false);
  const isReadOnly = ['completed', 'failed'].includes(sessionStatus);

  useEffect(() => {
    if (!sessionIdFromUrl || startingSession.current) return undefined;

    const controller = new AbortController();
    setInterviewStarted(true);
    setIsLoadingContext(true);
    setContextError('');
    setSubmissionError('');
    setCompletionError('');

    async function loadSession() {
      try {
        const { session } = await getInterviewSession(sessionIdFromUrl, controller.signal);
        const questions = session.questions.map(hydrateQuestion);
        setInterviewSessionId(session.id);
        setSessionStatus(session.status);
        setSelectedQuestionId((currentId) => (
          questions.some((question) => question.id === currentId)
            ? currentId
            : questions[0]?.id || ''
        ));
        const sources = [
          ...new Map(
            session.questions.flatMap((question) => question.knowledge_sources || [])
              .map((source) => [
                source.knowledge_source_id_snapshot,
                { id: source.knowledge_source_id_snapshot, title: source.source_title_snapshot },
              ]),
          ).values(),
        ];
        setInterviewContext({ sources, chunks: [], questions, restored: true });
      } catch (error) {
        if (error?.name !== 'AbortError') {
          setContextError(error.message || 'Unable to load this interview session.');
        }
      } finally {
        if (!controller.signal.aborted) setIsLoadingContext(false);
      }
    }

    loadSession();
    return () => controller.abort();
  }, [sessionIdFromUrl]);

  const startInterview = async () => {
    if (isLoadingContext) return;
    startingSession.current = true;
    setInterviewStarted(true);
    setIsLoadingContext(true);
    setContextError('');
    setInterviewContext(null);
    const session = getEmployeeSession();

    if (!session?.accessToken) {
      setContextError('Your Employee session is missing or expired. Please sign in again.');
      setIsLoadingContext(false);
      startingSession.current = false;
      return;
    }

    let sessionId;
    try {
      const { session: createdSession } = await createInterviewSession();
      sessionId = createdSession.id;
      setInterviewSessionId(sessionId);
      setSessionStatus(createdSession.status);
      const nextParams = new URLSearchParams(searchParams);
      nextParams.set('session_id', sessionId);
      setSearchParams(nextParams, { replace: true });

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
      const responseBody = await result.text();
      let data;
      try {
        data = JSON.parse(responseBody);
      } catch {
        if (result.status === 401) {
          throw new Error('Your Employee session is missing or expired. Please sign in again.');
        }
        throw new Error(result.ok
          ? 'The knowledge service returned an invalid response.'
          : `Unable to retrieve interview context (HTTP ${result.status}).`);
      }
      if (!result.ok) {
        if (result.status === 401) {
          throw new Error('Your Employee session is missing or expired. Please sign in again.');
        }
        throw new Error(typeof data?.detail === 'string'
          ? data.detail
          : 'Unable to retrieve interview context.');
      }

      if (!Array.isArray(data.chunks) || !Array.isArray(data.sources)) {
        throw new Error('The knowledge service returned invalid interview context.');
      }

      const generatedQuestions = buildInterviewQuestions(data.chunks);
      setInterviewContext({ ...data, questions: [] });
      const persistedQuestions = [];
      for (const question of generatedQuestions) {
        if (!question.source_id) {
          throw new Error('A generated question is missing its knowledge source.');
        }
        const { question: persistedQuestion } = await createInterviewQuestion(sessionId, {
          question_text: question.prompt,
          knowledge_source_ids: [question.source_id],
        });
        const hydratedQuestion = hydrateQuestion(persistedQuestion);
        persistedQuestions.push(hydratedQuestion);
        setSelectedQuestionId((currentId) => currentId || hydratedQuestion.id);
        setInterviewContext({ ...data, questions: [...persistedQuestions] });
      }
    } catch (error) {
      setContextError(error.message || 'Unable to retrieve interview context.');
      if (sessionId) {
        try {
          const { session } = await getInterviewSession(sessionId);
          setSessionStatus(session.status);
          setInterviewContext((currentContext) => currentContext && ({
            ...currentContext,
            questions: session.questions.map(hydrateQuestion),
          }));
          setSelectedQuestionId((currentId) => (
            session.questions.some((question) => question.id === currentId)
              ? currentId
              : session.questions[0]?.id || ''
          ));
        } catch (refreshError) {
          setContextError(
            `${error.message || 'Unable to save interview questions.'} `
            + `${refreshError.message || 'Saved progress could not be refreshed.'}`,
          );
        }
      }
    } finally {
      startingSession.current = false;
      setIsLoadingContext(false);
    }
  };

  const handleSubmitResponse = async (event) => {
    event.preventDefault();
    if (submittingAnswer.current || completingSession.current || isReadOnly) return;
    if (!response.trim()) {
      setSubmissionError('Enter a response before submitting.');
      return;
    }
    if (!interviewSessionId || !selectedQuestionId) {
      setSubmissionError('Select a question before submitting your response.');
      return;
    }

    submittingAnswer.current = true;
    setIsSubmitting(true);
    setSubmissionError('');
    try {
      const answer = await submitAnswer(
        interviewSessionId,
        selectedQuestionId,
        response,
      );
      setInterviewContext((currentContext) => currentContext && ({
        ...currentContext,
        questions: currentContext.questions.map((question) => (
          question.id === answer.question_id
            ? { ...question, answers: [...(question.answers || []), answer] }
            : question
        )),
      }));
      setResponse('');
    } catch (error) {
      setSubmissionError(error.message || 'Unable to submit your response.');
    } finally {
      submittingAnswer.current = false;
      setIsSubmitting(false);
    }
  };

  const handleCompleteSession = async () => {
    if (
      completingSession.current
      || submittingAnswer.current
      || isReadOnly
      || !interviewSessionId
    ) return;

    completingSession.current = true;
    setIsCompleting(true);
    setCompletionError('');
    try {
      const session = await completeSession(interviewSessionId);
      setSessionStatus(session.status);
      setInterviewContext((currentContext) => currentContext && ({
        ...currentContext,
        questions: session.questions.map(hydrateQuestion),
      }));
    } catch (error) {
      setCompletionError(error.message || 'Unable to finish this interview.');
    } finally {
      completingSession.current = false;
      setIsCompleting(false);
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

              <div className="workspace-actions">
                <div className={`workspace-status${isReadOnly ? ' is-read-only' : ''}`}>
                  <span />
                  {isLoadingContext
                    ? 'Loading context'
                    : contextError
                      ? 'Interview unavailable'
                      : isReadOnly
                        ? `Interview ${sessionStatus}`
                        : 'Interview Ready'}
                </div>
                {!isReadOnly && (
                  <button
                    className="finish-interview-button"
                    type="button"
                    onClick={handleCompleteSession}
                    disabled={
                      isLoadingContext
                      || isSubmitting
                      || isCompleting
                      || !sessionStatus
                      || !interviewSessionId
                    }
                  >
                    {isCompleting ? 'Finishing...' : 'Finish Interview'}
                  </button>
                )}
              </div>
            </div>

            {sessionStatus === 'completed' && (
              <div className="completion-banner" role="status">
                <CheckCircle2 size={17} />
                Interview completed. Questions and responses are now read-only.
              </div>
            )}
            {completionError && (
              <p className="completion-error" role="alert">{completionError}</p>
            )}

            <div className="workspace-context">
              <FileText size={15} />

              <span>
                {interviewContext
                  ? interviewContext.restored
                    ? `Loaded ${interviewContext.questions.length} saved question${interviewContext.questions.length === 1 ? '' : 's'} from this interview session.`
                    : `Retrieved ${interviewContext.chunks.length} text chunk${interviewContext.chunks.length === 1 ? '' : 's'} from ${interviewContext.sources.map((source) => source.title).join(', ')}.`
                  : 'Questions are generated only from text retrieved from saved knowledge sources.'}
              </span>
            </div>

            <div className="conversation-area">
              {isLoadingContext ? (
                <div className="conversation-placeholder" role="status">
                  <div className="placeholder-icon">
                    <Sparkles size={22} />
                  </div>
                  <h3>Loading interview session...</h3>
                  <p>Retrieving saved questions and process context.</p>
                </div>
              ) : contextError ? (
                <div className="conversation-placeholder" role="alert">
                  <div className="placeholder-icon">
                    <FileText size={22} />
                  </div>
                  <h3>{contextError}</h3>
                  <p>
                    {interviewContext
                      ? 'Reload this session to retrieve questions already saved.'
                      : 'Check your session and connection, then return to the Studio to start again.'}
                  </p>
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
                    {question.answers?.length > 0 && (
                      <div className="answer-history">
                        <strong>Response history</strong>
                        {question.answers.map((answer) => (
                          <p key={answer.id}>{answer.answer_text}</p>
                        ))}
                      </div>
                    )}
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

            <form className="response-area" onSubmit={handleSubmitResponse}>
              {isReadOnly ? (
                <p className="response-read-only" role="status">
                  This interview is {sessionStatus} and can no longer be changed.
                </p>
              ) : (
                <>
                  <label className="response-question-label" htmlFor="response-question">
                    Responding to
                    <select
                      id="response-question"
                      value={selectedQuestionId}
                      onChange={(event) => {
                        setSelectedQuestionId(event.target.value);
                        setSubmissionError('');
                      }}
                      disabled={
                        isLoadingContext
                        || isSubmitting
                        || isCompleting
                        || !interviewContext?.questions.length
                      }
                    >
                      {(interviewContext?.questions || []).map((question, index) => (
                        <option key={question.id} value={question.id}>
                          Question {index + 1}
                        </option>
                      ))}
                    </select>
                  </label>

                  <div className="response-entry">
                    <textarea
                      value={response}
                      onChange={(event) => {
                        setResponse(event.target.value);
                        if (submissionError) setSubmissionError('');
                      }}
                      placeholder="Your response will be entered here..."
                      disabled={
                        isLoadingContext
                        || isSubmitting
                        || isCompleting
                        || !selectedQuestionId
                      }
                      aria-invalid={Boolean(submissionError)}
                      aria-describedby={submissionError ? 'response-error' : undefined}
                    />

                    <button
                      className="send-response-button"
                      type="submit"
                      disabled={
                        isLoadingContext
                        || isSubmitting
                        || isCompleting
                        || !selectedQuestionId
                      }
                    >
                      <Send size={16} />
                      {isSubmitting ? 'Submitting...' : 'Submit Response'}
                    </button>
                  </div>
                  {submissionError && (
                    <p className="response-error" id="response-error" role="alert">
                      {submissionError}
                    </p>
                  )}
                </>
              )}
            </form>
          </section>
        )}
        </main>
      </div>
    </div>
  );
}

export default AIInterviewStudio;