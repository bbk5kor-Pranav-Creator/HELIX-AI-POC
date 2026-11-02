import { getEmployeeSession } from '../utils/auth';

const SESSION_PATH = '/api/interviews/sessions';
const UUID_PATTERN = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const AUTHENTICATION_ERROR =
  'Your Employee session is missing or expired. Please sign in again.';

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
    throw new Error(typeof data?.detail === 'string'
      ? data.detail
      : `Interview request failed (HTTP ${response.status}). Check the request and try again.`);
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

export async function createInterviewSession(signal) {
  const data = await requestInterview(SESSION_PATH, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
    signal,
  });
  if (!data?.session) {
    throw new Error('The interview service returned an invalid session.');
  }
  return { ...data, session: validateSession(data.session) };
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
