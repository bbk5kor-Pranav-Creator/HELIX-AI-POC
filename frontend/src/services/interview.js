import { getEmployeeSession } from '../utils/auth';

const SESSION_PATH = '/api/interviews/sessions';
const UUID_PATTERN = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const AUTHENTICATION_ERROR =
  'Your Employee session is missing or expired. Please sign in again.';
const ANALYSIS_DIMENSIONS = [
  'process_understanding',
  'role_clarity',
  'workflow_knowledge',
  'exception_handling',
  'knowledge_gaps',
];
const ANALYSIS_ASSESSMENTS = [
  'understood',
  'partially_understood',
  'not_understood',
  'insufficient_evidence',
];
const INTERVIEW_CATEGORY_IDS = [
  'docupedia',
  'docs',
  'audio',
  'video',
  'public-link',
  'paste-text',
];

function employeeHeaders() {
  const session = getEmployeeSession();
  if (!session?.accessToken) {
    throw new Error(AUTHENTICATION_ERROR);
  }
  return { Authorization: `Bearer ${session.accessToken}` };
}

async function requestInterview(path, options = {}) {
  const headers = { ...employeeHeaders(), ...options.headers };
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers,
      cache: 'no-store',
    });
  } catch (error) {
    if (error?.name === 'AbortError') throw error;
    throw new Error(
      'Unable to reach the interview service. Check your connection and retry.',
      { cause: error },
    );
  }

  const body = await response.text();
  let data;
  try {
    data = JSON.parse(body);
  } catch {
    if (response.status === 401) throw new Error(AUTHENTICATION_ERROR);
    throw new Error(response.ok
      ? 'The interview service returned an invalid response.'
      : `Interview request failed (HTTP ${response.status}). Please try again.`);
  }

  if (!response.ok) {
    if (response.status === 401) throw new Error(AUTHENTICATION_ERROR);
    const error = new Error(typeof data?.detail === 'string'
      ? data.detail
      : `Interview request failed (HTTP ${response.status}). Check the request and try again.`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function validateSession(session, expectedId) {
  if (
    typeof session?.id !== 'string'
    || !UUID_PATTERN.test(session.id)
    || (expectedId && session.id !== expectedId)
    || !Array.isArray(session.questions)
  ) {
    throw new Error('The interview service returned an invalid session.');
  }
  return session;
}

function validateAnalysisRecord(record, expectedSessionId) {
  if (
    typeof record?.id !== 'string'
    || !UUID_PATTERN.test(record.id)
    || record.session_id !== expectedSessionId
    || typeof record.created_at !== 'string'
    || typeof record.analysis !== 'object'
    || record.analysis === null
    || record.analysis.schema_version !== '1.0'
    || record.analysis.session_id !== expectedSessionId
    || typeof record.analysis.summary !== 'string'
    || typeof record.analysis.analysis_timestamp !== 'string'
    || typeof record.analysis.provider_metadata?.provider !== 'string'
    || typeof record.analysis.provider_metadata?.model !== 'string'
    || typeof record.analysis.provider_metadata?.prompt_version !== 'string'
    || !record.analysis.dimensions
    || ANALYSIS_DIMENSIONS.some((dimension) => {
      const value = record.analysis.dimensions[dimension];
      return !value
        || !ANALYSIS_ASSESSMENTS.includes(value.assessment)
        || typeof value.rationale !== 'string'
        || !Array.isArray(value.evidence);
    })
    || !Array.isArray(record.analysis.strengths)
    || !Array.isArray(record.analysis.improvement_areas)
    || !Array.isArray(record.analysis.identified_gaps)
    || !Array.isArray(record.analysis.recommended_follow_up_questions)
  ) {
    throw new Error('The interview service returned an invalid analysis record.');
  }
  return record;
}

export async function getInterviewCategories(signal) {
  const data = await requestInterview('/api/interviews/categories', { signal });
  if (
    !Array.isArray(data?.categories)
    || data.categories.some((category) => (
      !INTERVIEW_CATEGORY_IDS.includes(category?.id)
      || typeof category.name !== 'string'
      || !Number.isInteger(category.source_count)
      || category.source_count < 1
    ))
  ) {
    throw new Error('The interview service returned invalid knowledge categories.');
  }
  return data.categories;
}

export async function createInterviewSession(categoryId, signal) {
  if (!INTERVIEW_CATEGORY_IDS.includes(categoryId)) {
    throw new Error('Select an available knowledge category before starting.');
  }
  const data = await requestInterview(SESSION_PATH, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ category_id: categoryId }),
    signal,
  });
  if (!data?.session) {
    throw new Error('The interview service returned an invalid session.');
  }
  const session = validateSession(data.session);
  if (
    session.category_id !== categoryId
    || !Array.isArray(session.knowledge_source_ids)
    || session.knowledge_source_ids.some(
      (sourceId) => typeof sourceId !== 'string' || !UUID_PATTERN.test(sourceId),
    )
  ) {
    throw new Error('The interview service returned an invalid session scope.');
  }
  return { ...data, session };
}

export async function getInterviewSession(sessionId, signal) {
  if (typeof sessionId !== 'string' || !UUID_PATTERN.test(sessionId)) {
    throw new Error('A valid interview session ID is required.');
  }
  const data = await requestInterview(
    `${SESSION_PATH}/${encodeURIComponent(sessionId)}`,
    { signal },
  );
  if (!data?.session) {
    throw new Error('The interview service returned an invalid session.');
  }
  return { ...data, session: validateSession(data.session, sessionId) };
}

export async function createInterviewQuestion(
  sessionId,
  { question_text: questionText, knowledge_source_ids: knowledgeSourceIds },
  signal,
) {
  if (typeof sessionId !== 'string' || !UUID_PATTERN.test(sessionId)) {
    throw new Error('A valid interview session ID is required.');
  }
  if (
    typeof questionText !== 'string'
    || !questionText.trim()
    || !Array.isArray(knowledgeSourceIds)
    || knowledgeSourceIds.some(
      (sourceId) => typeof sourceId !== 'string' || !UUID_PATTERN.test(sourceId),
    )
  ) {
    throw new Error('The interview question or its knowledge sources are invalid.');
  }

  const data = await requestInterview(
    `${SESSION_PATH}/${encodeURIComponent(sessionId)}/questions`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question_text: questionText,
        knowledge_source_ids: knowledgeSourceIds,
      }),
      signal,
    },
  );
  if (
    typeof data?.question?.id !== 'string'
    || !Array.isArray(data.question.knowledge_sources)
  ) {
    throw new Error('The interview service returned an invalid question.');
  }
  return data;
}

export async function generateFirstQuestion(sessionId, signal) {
  if (typeof sessionId !== 'string' || !UUID_PATTERN.test(sessionId)) {
    throw new Error('A valid interview session ID is required.');
  }

  const data = await requestInterview(
    `${SESSION_PATH}/${encodeURIComponent(sessionId)}/first-question`,
    { method: 'POST', signal },
  );
  const question = data?.question;
  if (
    typeof question?.id !== 'string'
    || !UUID_PATTERN.test(question.id)
    || question.session_id !== sessionId
    || question.question_order !== 0
    || typeof question.question_text !== 'string'
    || !question.question_text.trim()
    || typeof question.created_at !== 'string'
    || !Array.isArray(question.knowledge_sources)
    || question.knowledge_sources.length === 0
    || question.knowledge_sources.some((source) => (
      !UUID_PATTERN.test(source.knowledge_source_id_snapshot)
      || typeof source.source_title_snapshot !== 'string'
      || !source.source_title_snapshot.trim()
      || (source.knowledge_source_id !== null
        && !UUID_PATTERN.test(source.knowledge_source_id))
    ))
    || !Array.isArray(question.answers)
  ) {
    throw new Error('The interview service returned an invalid first question.');
  }
  return { ...data, question };
}

export async function generateNextQuestion(sessionId, signal) {
  if (typeof sessionId !== 'string' || !UUID_PATTERN.test(sessionId)) {
    throw new Error('A valid interview session ID is required.');
  }

  const data = await requestInterview(
    `${SESSION_PATH}/${encodeURIComponent(sessionId)}/next-question`,
    { method: 'POST', signal },
  );
  if (typeof data?.should_continue !== 'boolean') {
    throw new Error('The interview service returned an invalid follow-up decision.');
  }
  if (!data.should_continue) {
    if (data.question !== null) {
      throw new Error('The interview service returned an invalid stop decision.');
    }
    return data;
  }

  const question = data.question;
  if (
    typeof question?.id !== 'string'
    || !UUID_PATTERN.test(question.id)
    || question.session_id !== sessionId
    || !Number.isInteger(question.question_order)
    || question.question_order < 1
    || typeof question.question_text !== 'string'
    || !question.question_text.trim()
    || typeof question.created_at !== 'string'
    || !Array.isArray(question.knowledge_sources)
    || question.knowledge_sources.length === 0
    || question.knowledge_sources.some((source) => (
      !UUID_PATTERN.test(source.knowledge_source_id_snapshot)
      || typeof source.source_title_snapshot !== 'string'
      || !source.source_title_snapshot.trim()
      || (source.knowledge_source_id !== null
        && !UUID_PATTERN.test(source.knowledge_source_id))
    ))
    || !Array.isArray(question.answers)
  ) {
    throw new Error('The interview service returned an invalid follow-up question.');
  }
  return { ...data, question };
}

export async function submitAnswer(sessionId, questionId, answerText, signal) {
  if (
    typeof sessionId !== 'string'
    || !UUID_PATTERN.test(sessionId)
    || typeof questionId !== 'string'
    || !UUID_PATTERN.test(questionId)
  ) {
    throw new Error('A valid interview session and question ID are required.');
  }
  if (typeof answerText !== 'string' || !answerText.trim()) {
    throw new Error('Enter a response before submitting.');
  }

  const data = await requestInterview(
    `${SESSION_PATH}/${encodeURIComponent(sessionId)}`
      + `/questions/${encodeURIComponent(questionId)}/answers`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answer_text: answerText }),
      signal,
    },
  );
  if (
    typeof data?.answer?.id !== 'string'
    || data.answer.question_id !== questionId
    || typeof data.answer.answer_text !== 'string'
  ) {
    throw new Error('The interview service returned an invalid answer.');
  }
  return data.answer;
}

export async function completeSession(sessionId, signal) {
  if (typeof sessionId !== 'string' || !UUID_PATTERN.test(sessionId)) {
    throw new Error('A valid interview session ID is required.');
  }

  const data = await requestInterview(
    `${SESSION_PATH}/${encodeURIComponent(sessionId)}/complete`,
    { method: 'POST', signal },
  );
  if (
    data?.session?.id !== sessionId
    || data.session.status !== 'completed'
    || !Array.isArray(data.session.questions)
  ) {
    throw new Error('The interview service returned an invalid completion status.');
  }
  return data.session;
}

export async function generateAnalysis(sessionId, signal) {
  if (typeof sessionId !== 'string' || !UUID_PATTERN.test(sessionId)) {
    throw new Error('A valid interview session ID is required.');
  }

  const data = await requestInterview(
    `${SESSION_PATH}/${encodeURIComponent(sessionId)}/analysis`,
    { method: 'POST', signal },
  );
  return validateAnalysisRecord(data, sessionId);
}

export async function getAnalysis(sessionId, signal) {
  if (typeof sessionId !== 'string' || !UUID_PATTERN.test(sessionId)) {
    throw new Error('A valid interview session ID is required.');
  }

  const data = await requestInterview(
    `${SESSION_PATH}/${encodeURIComponent(sessionId)}/analysis`,
    { signal },
  );
  if (
    data?.session_id !== sessionId
    || !Array.isArray(data.history)
    || (data.latest !== null && data.latest === undefined)
  ) {
    throw new Error('The interview service returned invalid analysis history.');
  }

  const history = data.history.map((record) => (
    validateAnalysisRecord(record, sessionId)
  ));
  const latest = data.latest === null
    ? null
    : validateAnalysisRecord(data.latest, sessionId);
  if (
    (history.length === 0 && latest !== null)
    || (history.length > 0 && latest?.id !== history[history.length - 1].id)
  ) {
    throw new Error('The interview service returned inconsistent analysis history.');
  }
  return { ...data, latest, history };
}
