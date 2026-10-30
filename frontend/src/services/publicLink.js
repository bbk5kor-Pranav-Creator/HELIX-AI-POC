import { getWorkspaceSession } from '../utils/auth';

function adminHeaders() {
  const session = getWorkspaceSession();
  if (!session?.accessToken || session.role !== 'Admin') {
    throw new Error('Your Admin session is missing or expired. Please sign in again.');
  }
  return {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${session.accessToken}`,
  };
}

export async function previewPublicLink({ url, title }, signal) {
  const normalizedUrl = typeof url === 'string' ? url.trim() : '';
  let parsedUrl;
  try {
    parsedUrl = new URL(normalizedUrl);
  } catch {
    throw new Error('Enter a valid public HTTP or HTTPS URL.');
  }
  if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
    throw new Error('Enter a valid public HTTP or HTTPS URL.');
  }
  if (typeof title === 'string' && title.trim().length > 120) {
    throw new Error('The title must be 120 characters or fewer.');
  }

  const headers = adminHeaders();
  let response;
  try {
    response = await fetch('/api/knowledge/public-link/preview', {
      method: 'POST',
      headers,
      body: JSON.stringify({
        url: normalizedUrl,
        title: typeof title === 'string' && title.trim() ? title.trim() : undefined,
      }),
      signal,
      cache: 'no-store',
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error(
      'Unable to reach the Public Link preview service. Check your connection and retry.',
      { cause: error },
    );
  }

  const body = await response.text();
  let data;
  try {
    data = JSON.parse(body);
  } catch {
    throw new Error(response.ok
      ? 'The Public Link service returned an invalid response.'
      : `Public Link preview failed (HTTP ${response.status}). Please try again.`);
  }

  if (!response.ok) {
    throw new Error(typeof data?.detail === 'string'
      ? data.detail
      : `Public Link preview failed (HTTP ${response.status}). Check the URL and retry.`);
  }
  if (
    data?.success !== true
    || typeof data.title !== 'string'
    || typeof data.url !== 'string'
    || typeof data.source_url !== 'string'
    || typeof data.generated_content !== 'string'
    || typeof data.cleaned_content !== 'string'
    || !data.metadata
    || typeof data.metadata !== 'object'
    || Array.isArray(data.metadata)
  ) {
    throw new Error('The Public Link service returned an invalid preview.');
  }
  return data;
}

export async function savePublicLink({ url, title, text, metadata }, signal) {
  const normalizedUrl = typeof url === 'string' ? url.trim() : '';
  let parsedUrl;
  try {
    parsedUrl = new URL(normalizedUrl);
  } catch {
    throw new Error('Enter a valid public HTTP or HTTPS URL.');
  }
  if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
    throw new Error('Enter a valid public HTTP or HTTPS URL.');
  }
  if (typeof text !== 'string' || !text.trim()) {
    throw new Error('Reviewed content must not be blank.');
  }
  if (text.length > 200000) {
    throw new Error('Reviewed content must be 200,000 characters or fewer.');
  }
  if (typeof title === 'string' && title.trim().length > 120) {
    throw new Error('The title must be 120 characters or fewer.');
  }

  const headers = adminHeaders();
  let response;
  try {
    response = await fetch('/api/knowledge/public-link', {
      method: 'POST',
      headers,
      body: JSON.stringify({
        url: normalizedUrl,
        title: typeof title === 'string' && title.trim() ? title.trim() : undefined,
        text,
        metadata,
      }),
      signal,
      cache: 'no-store',
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error(
      'Unable to reach the Public Link save service. Check your connection and retry.',
      { cause: error },
    );
  }

  const body = await response.text();
  let data;
  try {
    data = JSON.parse(body);
  } catch {
    throw new Error(response.ok
      ? 'The Public Link service returned an invalid save response.'
      : `Public Link save failed (HTTP ${response.status}). Please try again.`);
  }

  if (!response.ok) {
    throw new Error(typeof data?.detail === 'string'
      ? data.detail
      : `Public Link save failed (HTTP ${response.status}). Check the content and retry.`);
  }
  if (
    data?.success !== true
    || typeof data.source?.id !== 'string'
    || data.source.source_type !== 'Public Link'
  ) {
    throw new Error('The Public Link service did not confirm persistence.');
  }
  return data.source;
}
