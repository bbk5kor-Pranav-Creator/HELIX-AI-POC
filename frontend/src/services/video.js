import { getWorkspaceSession } from '../utils/auth';

function adminHeaders() {
  const session = getWorkspaceSession();
  if (!session?.accessToken || session.role !== 'Admin') {
    throw new Error('Your Admin session is missing or expired. Please sign in again.');
  }
  return { Authorization: `Bearer ${session.accessToken}` };
}

async function responseError(response) {
  const data = await response.json();
  return new Error(typeof data.detail === 'string' ? data.detail : 'Video request failed. Check the recording and try again.');
}

export async function processVideo(file, signal, onStage, resumeId) {
  const body = new FormData();
  body.append('file', file);
  if (resumeId) body.append('resume_id', resumeId);
  const response = await fetch('/api/knowledge/video/process', {
    method: 'POST', headers: adminHeaders(), body, signal,
  });
  if (!response.ok) throw await responseError(response);
  if (!response.body) throw new Error('Streaming video progress is unavailable in this browser.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let result = null;
  const handleLine = (line) => {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    if (event.stage === 'error') throw new Error(event.message || 'Video processing failed.');
    if (event.stage === 'complete') {
      if (typeof event.text !== 'string' || typeof event.filename !== 'string') {
        throw new Error('The video service returned an invalid preview.');
      }
      result = event;
    } else {
      onStage(event);
    }
  };
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      lines.forEach(handleLine);
      if (done) break;
    }
    if (buffer.trim()) handleLine(buffer);
    if (!result) throw new Error('Video processing ended before a preview was received. Check the backend and retry.');
    return result;
  } finally {
    await reader.cancel();
    reader.releaseLock();
  }
}

export async function saveVideo({ title, text, notes, filename }) {
  const response = await fetch('/api/knowledge/video', {
    method: 'POST',
    headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: title.trim() || undefined, text, notes, filename }),
  });
  if (!response.ok) throw await responseError(response);
  return (await response.json()).source;
}
