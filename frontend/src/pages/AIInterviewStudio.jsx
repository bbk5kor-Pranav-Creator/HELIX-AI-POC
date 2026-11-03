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
  createInterviewSession,
  completeSession,
  getInterviewCategories,
  getInterviewSession,
  generateFirstQuestion,
  generateNextQuestion,
  generateAnalysis,
  getAnalysis,
  submitAnswer,
} from '../services/interview';
import InterviewAnalysisPanel from '../components/common/InterviewAnalysisPanel';
import { getEmployeeSession } from '../utils/auth';
import './employee.css';
import './ai-interview-studio.css';

function hydrateQuestion(question) {
  const sources = question.knowledge_sources || [];
  return {
    id: question.id,
    question_order: question.question_order,
    title: sources.map((source) => source.source_title_snapshot).join(', ')
      || 'Knowledge source',
    prompt: question.question_text,
    source_id: sources[0]?.knowledge_source_id_snapshot,
    knowledge_sources: sources,
    answers: question.answers || [],
  };
}

function interviewContextFromQuestion(question, categoryId) {
  const hydratedQuestion = hydrateQuestion(question);
  const sources = [
    ...new Map(
      (question.knowledge_sources || []).map((source) => [
        source.knowledge_source_id_snapshot,
        {
          id: source.knowledge_source_id_snapshot,
          title: source.source_title_snapshot,
        },
      ]),
    ).values(),
  ];
  return {
    sources,
    chunks: [],
    questions: [hydratedQuestion],
    categoryId,
    restored: true,
  };
}

function describeAnalysisError(error, operation) {
  if (operation === 'generate' && [502, 503].includes(error?.status)) {
    return {
      kind: 'provider',
      title: 'Analysis provider unavailable',
      message: 'The analysis service could not complete this request. Your interview answers were not changed.',
    };
  }
  if (
    operation === 'generate'
    && (error?.status === 422 || /invalid analysis/i.test(error?.message || ''))
  ) {
    return {
      kind: 'validation',
      title: 'Analysis could not be validated',
      message: 'No analysis was saved. You can try generating it again.',
    };
  }
  if (operation === 'generate' && error?.status === 413) {
    return {
      kind: 'validation',
      title: 'Interview context is too large',
      message: 'The analysis was not generated because the complete evidence exceeds the configured limit.',
    };
  }
  return {
    kind: 'load',
    title: operation === 'load' ? 'Saved analysis could not be loaded' : 'Analysis request failed',
    message: error?.message || 'Please try again.',
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
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isGeneratingNextQuestion, setIsGeneratingNextQuestion] = useState(false);
  const [nextQuestionError, setNextQuestionError] = useState('');
  const [questioningComplete, setQuestioningComplete] = useState(false);
  const [isCompleting, setIsCompleting] = useState(false);
  const [submissionError, setSubmissionError] = useState('');
  const [analysisHistory, setAnalysisHistory] = useState([]);
  const [selectedAnalysisId, setSelectedAnalysisId] = useState('');
  const [analysisStatus, setAnalysisStatus] = useState('idle');
  const [analysisError, setAnalysisError] = useState(null);
  const [interviewCategories, setInterviewCategories] = useState([]);
  const [selectedCategoryId, setSelectedCategoryId] = useState('');
  const [isLoadingCategories, setIsLoadingCategories] = useState(true);
  const [categoryError, setCategoryError] = useState('');
  const startingSession = useRef(false);
  const submittingAnswer = useRef(false);
  const generatingNextQuestion = useRef(false);
  const completingSession = useRef(false);
  const generatingAnalysis = useRef(false);
  const isReadOnly = ['completed', 'failed'].includes(sessionStatus);
  const conversationQuestions = interviewContext?.questions || [];
  const currentQuestion = conversationQuestions.at(-1) || null;
  const currentQuestionAnswered = Boolean(currentQuestion?.answers?.length);
  const selectedCategoryName = interviewCategories.find(
    (category) => category.id === selectedCategoryId,
  )?.name || ({
    docupedia: 'Docupedia',
    docs: 'Docs',
    audio: 'Audio',
    video: 'Video',
    'public-link': 'Public Link',
    'paste-text': 'Paste Text',
  }[selectedCategoryId || interviewContext?.categoryId])
    || 'Knowledge category unavailable';

  useEffect(() => {
    const controller = new AbortController();
    async function loadCategories() {
      try {
        const categories = await getInterviewCategories(controller.signal);
        if (!controller.signal.aborted) setInterviewCategories(categories);
      } catch (error) {
        if (error?.name !== 'AbortError' && !controller.signal.aborted) {
          setCategoryError(error.message || 'Unable to load interview categories.');
        }
      } finally {
        if (!controller.signal.aborted) setIsLoadingCategories(false);
      }
    }
    loadCategories();
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!sessionIdFromUrl || startingSession.current) return undefined;

    const controller = new AbortController();
    setInterviewStarted(true);
    setIsLoadingContext(true);
    setContextError('');
    setSubmissionError('');
    setNextQuestionError('');
    setQuestioningComplete(false);
    setCompletionError('');
    setAnalysisHistory([]);
    setSelectedAnalysisId('');
    setAnalysisError(null);
    setAnalysisStatus('idle');

    async function loadSession() {
      try {
        const { session } = await getInterviewSession(sessionIdFromUrl, controller.signal);
        const questions = session.questions.map(hydrateQuestion);
        setInterviewSessionId(session.id);
        setSessionStatus(session.status);
        setSelectedCategoryId(session.category_id || '');
        const sources = [
          ...new Map(
            session.questions.flatMap((question) => question.knowledge_sources || [])
              .map((source) => [
                source.knowledge_source_id_snapshot,
                { id: source.knowledge_source_id_snapshot, title: source.source_title_snapshot },
              ]),
          ).values(),
        ];
        setInterviewContext({
          sources,
          chunks: [],
          questions,
          categoryId: session.category_id,
          restored: true,
        });
        if (session.status === 'completed') {
          setAnalysisStatus('loading');
          try {
            const analysisResponse = await getAnalysis(
              sessionIdFromUrl,
              controller.signal,
            );
            if (controller.signal.aborted) return;
            setAnalysisHistory(analysisResponse.history);
            setSelectedAnalysisId(analysisResponse.latest?.id || '');
            setAnalysisStatus(analysisResponse.latest ? 'ready' : 'empty');
          } catch (error) {
            if (error?.name !== 'AbortError' && !controller.signal.aborted) {
              setAnalysisError(describeAnalysisError(error, 'load'));
              setAnalysisStatus('error');
            }
          }
        }
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
    if (startingSession.current || isLoadingContext) return;
    startingSession.current = true;
    setInterviewStarted(true);
    setIsLoadingContext(true);
    setContextError('');
    setInterviewContext(null);
    setCategoryError('');
    setNextQuestionError('');
    setQuestioningComplete(false);
    setAnalysisHistory([]);
    setSelectedAnalysisId('');
    setAnalysisError(null);
    setAnalysisStatus('idle');
    const session = getEmployeeSession();

    if (!session?.accessToken) {
      setContextError('Your Employee session is missing or expired. Please sign in again.');
      setIsLoadingContext(false);
      startingSession.current = false;
      return;
    }

    let sessionId;
    try {
      if (!selectedCategoryId) {
        throw new Error('Select a knowledge category before starting the interview.');
      }
      const { session: createdSession } = await createInterviewSession(selectedCategoryId);
      sessionId = createdSession.id;
      setInterviewSessionId(sessionId);
      setSessionStatus(createdSession.status);
      const nextParams = new URLSearchParams(searchParams);
      nextParams.set('session_id', sessionId);
      setSearchParams(nextParams, { replace: true });

      const { question } = await generateFirstQuestion(sessionId);
      setInterviewContext(interviewContextFromQuestion(question, selectedCategoryId));
    } catch (error) {
      setContextError(error.message || 'Unable to retrieve interview context.');
      if (sessionId) {
        try {
          const { session } = await getInterviewSession(sessionId);
          setSessionStatus(session.status);
          setSelectedCategoryId(session.category_id || selectedCategoryId);
          const questions = session.questions.map(hydrateQuestion);
          const sources = [
            ...new Map(
              session.questions.flatMap((question) => question.knowledge_sources || [])
                .map((source) => [
                  source.knowledge_source_id_snapshot,
                  { id: source.knowledge_source_id_snapshot, title: source.source_title_snapshot },
                ]),
            ).values(),
          ];
          setInterviewContext({
            sources,
            chunks: [],
            questions,
            categoryId: session.category_id,
            restored: true,
          });
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

  const retryFirstQuestion = async () => {
    if (
      startingSession.current
      || isLoadingContext
      || !interviewSessionId
      || isReadOnly
      || interviewContext?.questions.length
    ) return;

    startingSession.current = true;
    setIsLoadingContext(true);
    setContextError('');
    try {
      const { question } = await generateFirstQuestion(interviewSessionId);
      setInterviewContext(interviewContextFromQuestion(question, selectedCategoryId));
    } catch (error) {
      setContextError(error.message || 'Unable to generate the first interview question.');
    } finally {
      startingSession.current = false;
      setIsLoadingContext(false);
    }
  };

  const handleSubmitResponse = async (event) => {
    event.preventDefault();
    if (
      submittingAnswer.current
      || completingSession.current
      || generatingNextQuestion.current
      || isReadOnly
    ) return;
    if (!response.trim()) {
      setSubmissionError('Enter a response before submitting.');
      return;
    }
    const currentQuestion = interviewContext?.questions.at(-1);
    if (!interviewSessionId || !currentQuestion) {
      setSubmissionError('Select a question before submitting your response.');
      return;
    }
    if (currentQuestion.answers?.length) {
      setSubmissionError('This question already has a saved answer. Request the next question instead.');
      return;
    }

    submittingAnswer.current = true;
    setIsSubmitting(true);
    setSubmissionError('');
    setNextQuestionError('');
    try {
      const answer = await submitAnswer(
        interviewSessionId,
        currentQuestion.id,
        response,
      );
      setInterviewContext((currentContext) => currentContext && ({
        ...currentContext,
        questions: currentContext.questions.map((question) => (
          question.id === answer.question_id
            ? {
              ...question,
              answers: question.answers?.some((item) => item.id === answer.id)
                ? question.answers
                : [...(question.answers || []), answer],
            }
            : question
        )),
      }));
      setResponse('');
      await requestNextQuestion();
    } catch (error) {
      setSubmissionError(error.message || 'Unable to submit your response.');
    } finally {
      submittingAnswer.current = false;
      setIsSubmitting(false);
    }
  };

  const requestNextQuestion = async () => {
    if (
      generatingNextQuestion.current
      || !interviewSessionId
      || isReadOnly
    ) return;

    generatingNextQuestion.current = true;
    setIsGeneratingNextQuestion(true);
    setNextQuestionError('');
    try {
      const result = await generateNextQuestion(interviewSessionId);
      if (!result.should_continue) {
        setQuestioningComplete(true);
        return;
      }

      const nextQuestion = hydrateQuestion(result.question);
      setInterviewContext((currentContext) => {
        if (!currentContext) return currentContext;
        const alreadyPresent = currentContext.questions.some(
          (question) => question.id === nextQuestion.id,
        );
        const sources = [
          ...new Map([
            ...(currentContext.sources || []).map((source) => [source.id, source]),
            ...nextQuestion.knowledge_sources.map((source) => [
              source.knowledge_source_id_snapshot,
              {
                id: source.knowledge_source_id_snapshot,
                title: source.source_title_snapshot,
              },
            ]),
          ]).values(),
        ];
        return {
          ...currentContext,
          sources,
          questions: alreadyPresent
            ? currentContext.questions
            : [...currentContext.questions, nextQuestion],
        };
      });
      setQuestioningComplete(false);
    } catch (error) {
      setNextQuestionError(
        error.message || 'Unable to generate the next interview question.',
      );
    } finally {
      generatingNextQuestion.current = false;
      setIsGeneratingNextQuestion(false);
    }
  };

  const handleCompleteSession = async () => {
    if (
      completingSession.current
      || submittingAnswer.current
      || generatingNextQuestion.current
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
      setAnalysisHistory([]);
      setSelectedAnalysisId('');
      setAnalysisError(null);
      setAnalysisStatus('empty');
    } catch (error) {
      setCompletionError(error.message || 'Unable to finish this interview.');
    } finally {
      completingSession.current = false;
      setIsCompleting(false);
    }
  };

  const handleGenerateAnalysis = async () => {
    if (
      generatingAnalysis.current
      || sessionStatus !== 'completed'
      || !interviewSessionId
    ) return;

    generatingAnalysis.current = true;
    setAnalysisStatus('generating');
    setAnalysisError(null);
    try {
      const record = await generateAnalysis(interviewSessionId);
      setAnalysisHistory((currentHistory) => [
        ...currentHistory.filter((item) => item.id !== record.id),
        record,
      ]);
      setSelectedAnalysisId(record.id);
      setAnalysisStatus('ready');
    } catch (error) {
      setAnalysisError(describeAnalysisError(error, 'generate'));
      setAnalysisStatus(analysisHistory.length ? 'ready' : 'error');
    } finally {
      generatingAnalysis.current = false;
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

            <div className="interview-category-picker">
              <label htmlFor="interview-category">Knowledge category</label>
              <select
                id="interview-category"
                value={selectedCategoryId}
                onChange={(event) => setSelectedCategoryId(event.target.value)}
                disabled={isLoadingCategories || interviewCategories.length === 0}
                aria-describedby={categoryError ? 'interview-category-error' : undefined}
              >
                <option value="">
                  {isLoadingCategories ? 'Loading categories...' : 'Select a category'}
                </option>
                {interviewCategories.map((category) => (
                  <option key={category.id} value={category.id}>
                    {category.name} · {category.source_count} source{category.source_count === 1 ? '' : 's'}
                  </option>
                ))}
              </select>
              {categoryError ? (
                <p className="interview-category-message is-error" id="interview-category-error" role="alert">
                  {categoryError}
                </p>
              ) : !isLoadingCategories && interviewCategories.length === 0 ? (
                <p className="interview-category-message" role="status">
                  No interview-ready knowledge is available yet.
                </p>
              ) : (
                <p className="interview-category-message">
                  Questions will use knowledge from this category only.
                </p>
              )}
            </div>

            <button
              className="start-interview-button"
              type="button"
              onClick={startInterview}
              disabled={
                isLoadingContext
                || isLoadingCategories
                || !selectedCategoryId
                || interviewCategories.length === 0
              }
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
                      || isGeneratingNextQuestion
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
                {`Category: ${selectedCategoryName}. `}
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
                  {interviewSessionId
                    && ['created', 'in_progress'].includes(sessionStatus)
                    && !interviewContext?.questions.length && (
                      <button
                        className="start-interview-button"
                        type="button"
                        onClick={retryFirstQuestion}
                        disabled={isLoadingContext}
                      >
                        Retry first question
                      </button>
                  )}
                </div>
              ) : (
                <div className="conversation-thread" aria-live="polite">
                  {conversationQuestions.length ? (
                    conversationQuestions.map((question, index) => (
                      <article className="conversation-turn" key={question.id}>
                        <div
                          className="conversation-message is-question"
                          id={`interview-question-${question.id}`}
                        >
                          <span className="conversation-speaker">Helix · Question {index + 1}</span>
                          <p>{question.prompt}</p>
                          <div className="conversation-sources">
                            <FileText size={13} aria-hidden="true" />
                            <span>
                              {question.knowledge_sources?.length
                                ? question.knowledge_sources.map((source) => (
                                  <span
                                    className="conversation-source"
                                    key={source.knowledge_source_id_snapshot}
                                  >
                                    {source.source_title_snapshot}
                                  </span>
                                ))
                                : 'No source reference'}
                            </span>
                          </div>
                        </div>

                        {question.answers?.map((answer) => (
                          <div
                            className="conversation-message is-answer"
                            id={`interview-answer-${answer.id}`}
                            key={answer.id}
                          >
                            <span className="conversation-speaker">You</span>
                            <p>{answer.answer_text}</p>
                          </div>
                        ))}
                      </article>
                    ))
                  ) : (
                    <div className="conversation-placeholder">
                      <div className="placeholder-icon">
                        <Sparkles size={22} />
                      </div>
                      <h3>No interview questions are available</h3>
                      <p>Retry first-question generation or return to the Studio.</p>
                    </div>
                  )}

                  {isGeneratingNextQuestion && (
                    <div className="conversation-message is-question conversation-typing" role="status">
                      <span className="conversation-speaker">Helix is considering your answer</span>
                      <span className="typing-indicator" aria-label="Generating next question">
                        <i /><i /><i />
                      </span>
                    </div>
                  )}

                  {questioningComplete && (
                    <div className="conversation-stop-message" role="status">
                      <CheckCircle2 size={17} />
                      <span>Sufficient information has been gathered. Review the conversation and finish the interview when ready.</span>
                    </div>
                  )}
                </div>
              )}
            </div>

            {nextQuestionError && (
              <div className="next-question-error" role="alert">
                <p>{nextQuestionError}</p>
                <button
                  className="next-question-retry-button"
                  type="button"
                  onClick={requestNextQuestion}
                  disabled={isGeneratingNextQuestion || isReadOnly}
                >
                  Retry next question
                </button>
              </div>
            )}

            {sessionStatus === 'completed' && (
              <InterviewAnalysisPanel
                history={analysisHistory}
                selectedAnalysisId={selectedAnalysisId}
                onSelectAnalysis={setSelectedAnalysisId}
                status={analysisStatus}
                error={analysisError}
                onGenerate={handleGenerateAnalysis}
                isGenerating={analysisStatus === 'generating'}
                questions={interviewContext?.questions || []}
              />
            )}

            <form className="response-area" onSubmit={handleSubmitResponse}>
              {isReadOnly ? (
                <p className="response-read-only" role="status">
                  This interview is {sessionStatus} and can no longer be changed.
                </p>
              ) : questioningComplete ? (
                <p className="response-read-only" role="status">
                  Gemini has no further meaningful questions. You can finish the interview manually.
                </p>
              ) : currentQuestionAnswered ? (
                <div className="next-question-control">
                  <p>
                    {nextQuestionError
                      ? 'Your answer is saved. Retry generation without submitting it again.'
                      : 'Your answer is saved. Continue when you are ready for the next question.'}
                  </p>
                  {!nextQuestionError && (
                    <button
                      className="send-response-button"
                      type="button"
                      onClick={requestNextQuestion}
                      disabled={isLoadingContext || isGeneratingNextQuestion || isCompleting}
                    >
                      <Sparkles size={16} />
                      {isGeneratingNextQuestion ? 'Generating...' : 'Continue interview'}
                    </button>
                  )}
                </div>
              ) : currentQuestion ? (
                <>
                  <label className="response-question-label" htmlFor="response-answer">
                    Your answer
                  </label>

                  <div className="response-entry">
                    <textarea
                      id="response-answer"
                      value={response}
                      onChange={(event) => {
                        setResponse(event.target.value);
                        if (submissionError) setSubmissionError('');
                      }}
                      placeholder="Write your answer..."
                      disabled={
                        isLoadingContext
                        || isSubmitting
                        || isGeneratingNextQuestion
                        || isCompleting
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
                        || isGeneratingNextQuestion
                        || isCompleting
                        || !response.trim()
                      }
                    >
                      <Send size={16} />
                      {isSubmitting ? 'Saving...' : 'Submit answer'}
                    </button>
                  </div>
                  {submissionError && (
                    <p className="response-error" id="response-error" role="alert">
                      {submissionError}
                    </p>
                  )}
                </>
              ) : (
                <p className="response-read-only" role="status">
                  Generate the first persisted interview question to begin.
                </p>
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