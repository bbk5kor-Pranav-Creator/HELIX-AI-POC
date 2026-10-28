import { getWorkspaceSession } from '../utils/auth';

function adminHeaders() {
  const session = getWorkspaceSession();
  if (!session?.accessToken || session.role !== 'Admin') {
    throw new Error('Your Admin session is missing or expired. Please sign in again.');
  }
  return { Authorization: `Bearer ${session.accessToken}` };
}

function jobPath(jobId) {
  if (typeof jobId !== 'string' || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(jobId)) {
    throw new Error('A valid video job ID is required.');
  }
  return `/api/knowledge/video/jobs/${jobId}`;
}

async function requestVideo(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...adminHeaders(), ...options.headers },
    cache: 'no-store',
  });
  if (response.status === 204 && response.ok) return undefined;
  const body = await response.text();
  let data;
  try {
    data = JSON.parse(body);
  } catch {
    throw new Error(response.ok
      ? 'The video service returned an invalid response.'
      : `Video request failed (HTTP ${response.status}). Please try again.`);
  }
  if (!response.ok) {
    throw new Error(typeof data?.detail === 'string'
      ? data.detail
      : `Video request failed (HTTP ${response.status}). Check the request and try again.`);
  }
  return data;
}

function validateJob(job, expectedId) {
  jobPath(job?.job_id);
  if ((expectedId && job.job_id !== expectedId)
    || !['queued', 'processing', 'completed', 'failed'].includes(job.state)) {
    throw new Error('The video service returned an invalid job status.');
  }
  return job;
}

function waitForPoll(seconds, signal) {
  signal?.throwIfAborted();
  const delay = Number.isFinite(seconds) && seconds > 0
    ? Math.min(30, Math.max(1, seconds)) : 2;
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', abort);
      reject(signal.reason || new DOMException('Polling stopped.', 'AbortError'));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', abort);
      resolve();
    }, delay * 1000);
    signal?.addEventListener('abort', abort, { once: true });
  });
}

export async function uploadVideo(file, signal) {
  const body = new FormData();
  body.append('file', file);
  const job = await requestVideo('/api/knowledge/video/process', {
    method: 'POST', body, signal,
  });
  return validateJob(job);
}

export async function getVideoJob(jobId, signal) {
  return validateJob(await requestVideo(jobPath(jobId), { signal }), jobId);
}

export async function getVideoResult(jobId, signal) {
  const result = await requestVideo(`${jobPath(jobId)}/result`, { signal });
  if (result?.job_id !== jobId || typeof result.text !== 'string'
    || typeof result.filename !== 'string') {
    throw new Error('The video service returned an invalid preview.');
  }
  return result;
}

// Aborting stops upload/polling, not extraction already accepted by the backend.
export async function processVideo(file, signal, onStage) {
  let job = await uploadVideo(file, signal);
  while (true) {
    signal?.throwIfAborted();
    onStage?.({
      ...job,
      stage: job.state,
      message: job.message || ({
        queued: 'Video is queued for local processing.',
        processing: 'Transcribing video speech locally.',
        completed: 'Video transcription completed.',
        failed: 'Video extraction failed.',
      })[job.state],
    });
    if (job.state === 'failed') {
      throw new Error(typeof job.error?.message === 'string'
        ? job.error.message : 'Video extraction failed. Check the recording and try again.');
    }
    if (job.state === 'completed') return getVideoResult(job.job_id, signal);
    await waitForPoll(job.poll_after_seconds, signal);
    job = await getVideoJob(job.job_id, signal);
  }
}

export async function saveVideo({ job_id: jobId, title, text, notes }, signal) {
  jobPath(jobId);
  const data = await requestVideo('/api/knowledge/video', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: jobId, title: title?.trim() || undefined, text, notes }),
    signal,
  });
  if (data?.success !== true || typeof data.source?.id !== 'string') {
    throw new Error('The video service returned an invalid save confirmation.');
  }
  return data.source;
}

export async function discardVideo(jobId, signal) {
  await requestVideo(jobPath(jobId), { method: 'DELETE', signal });
}
