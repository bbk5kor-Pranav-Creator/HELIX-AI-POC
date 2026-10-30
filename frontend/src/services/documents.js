import { getWorkspaceSession } from '../utils/auth';

function adminHeaders() {
  const session = getWorkspaceSession();
  if (!session?.accessToken || session.role !== 'Admin') {
    throw new Error('Your Admin session is missing or expired. Please sign in again.');
  }
  return { Authorization: 'Bearer ' + session.accessToken };
}

function validatePreview(data, expectedCount) {
  if (!Array.isArray(data?.results) || data.results.length !== expectedCount) {
    throw new Error('The document service returned an invalid preview.');
  }

  return data.results.map((result) => {
    if (
      typeof result?.filename !== 'string'
      || !['success', 'error'].includes(result.status)
      || (result.status === 'success' && typeof result.text !== 'string')
      || (result.status === 'error' && typeof result.error !== 'string')
    ) {
      throw new Error('The document service returned an invalid file result.');
    }
    return result;
  });
}

export async function previewDocuments(files, signal) {
  if (!Array.isArray(files) || files.length === 0) {
    throw new Error('Select at least one document to extract.');
  }

  const body = new FormData();
  files.forEach((file) => body.append('files', file));

  let response;
  try {
    response = await fetch('/api/knowledge/documents/preview', {
      method: 'POST',
      headers: adminHeaders(),
      body,
      signal,
      cache: 'no-store',
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error(
      'Unable to reach the document extraction service. Check your connection and retry.',
      { cause: error },
    );
  }

  const responseBody = await response.text();
  let data;
  try {
    data = JSON.parse(responseBody);
  } catch {
    throw new Error(response.ok
      ? 'The document service returned an invalid response.'
      : `Document extraction failed (HTTP ${response.status}). Please try again.`);
  }

  if (!response.ok) {
    throw new Error(typeof data?.detail === 'string'
      ? data.detail
      : `Document extraction failed (HTTP ${response.status}). Check the files and retry.`);
  }

  return validatePreview(data, files.length);
}

export async function saveDocuments(documents, signal) {
  if (!Array.isArray(documents) || documents.length === 0) {
    throw new Error('Select at least one extracted document to save.');
  }
  if (documents.length > 20) {
    throw new Error('Save no more than 20 documents in one request.');
  }
  documents.forEach((document) => {
    if (
      typeof document?.filename !== 'string'
      || typeof document?.text !== 'string'
      || !document.text.trim()
      || document.text.length > 200000
      || typeof document?.idempotency_key !== 'string'
    ) {
      throw new Error('A document has invalid or empty extracted content.');
    }
  });

  let response;
  try {
    response = await fetch('/api/knowledge/documents/save', {
      method: 'POST',
      headers: {
        ...adminHeaders(),
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ documents }),
      signal,
      cache: 'no-store',
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error(
      'Unable to reach the document save service. Check your connection and retry.',
      { cause: error },
    );
  }

  const responseBody = await response.text();
  let data;
  try {
    data = JSON.parse(responseBody);
  } catch {
    throw new Error(response.ok
      ? 'The document service returned an invalid save response.'
      : `Document save failed (HTTP ${response.status}). Please try again.`);
  }

  if (!response.ok) {
    throw new Error(typeof data?.detail === 'string'
      ? data.detail
      : `Document save failed (HTTP ${response.status}). Check the content and retry.`);
  }
  if (!Array.isArray(data?.results) || data.results.length !== documents.length) {
    throw new Error('The document service returned an invalid save result.');
  }

  return data.results.map((result, index) => {
    if (
      result?.filename !== documents[index].filename
      || !['success', 'error'].includes(result.status)
      || (result.status === 'success'
        && (typeof result.source?.id !== 'string'
          || result.source.source_type !== 'Document'))
      || (result.status === 'error' && typeof result.error !== 'string')
    ) {
      throw new Error('The document service returned an invalid file save result.');
    }
    return result;
  });
}
