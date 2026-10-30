import { getWorkspaceSession } from '../utils/auth';

export const DOCUPEDIA_FORMATS = '.pdf,.docx,.doc,.txt';
export const DOCUPEDIA_FILE_LIMIT = 50 * 1024 * 1024;
export const DOCUPEDIA_APPROVAL_LIMIT = 200000;

async function request(path, { body, signal, method = 'POST' } = {}) {
  const session = getWorkspaceSession();
  if (!session?.accessToken || session.role !== 'Admin') {
    throw new Error('Your Admin session is missing or expired. Please sign in again.');
  }
  const headers = { Authorization: `Bearer ${session.accessToken}` };
  const multipart = body instanceof FormData;
  if (body && !multipart) headers['Content-Type'] = 'application/json';
  let response;
  try {
    response = await fetch(`/api/knowledge/docupedia${path}`, {
      method, headers, body: body && (multipart ? body : JSON.stringify(body)),
      signal, cache: 'no-store',
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('Unable to reach Docupedia. Check your connection and retry.', { cause: error });
  }
  let data;
  try {
    data = JSON.parse(await response.text());
  } catch {
    throw new Error(`Docupedia returned an invalid response (HTTP ${response.status}). Retry the same request.`);
  }
  if (!response.ok) {
    const detail = typeof data?.detail === 'string'
      ? data.detail
      : Array.isArray(data?.detail)
        ? data.detail.map((item) => item.msg).join('; ')
        : `Docupedia request failed (HTTP ${response.status}).`;
    const error = new Error(response.status === 409
      ? `Conflict: ${detail} Reload the saved document to check its current status.`
      : detail);
    error.status = response.status;
    throw error;
  }
  return data;
}

function validateStructured(document) {
  if (
    !document || typeof document.original_text !== 'string'
    || (document.reviewed_text !== undefined && typeof document.reviewed_text !== 'string')
    || !['pdf', 'docx', 'doc', 'txt'].includes(document.format)
    || !Array.isArray(document.blocks) || !Array.isArray(document.warnings)
    || document.blocks.some((block) => (
      !Number.isInteger(block?.ordinal) || typeof block.block_type !== 'string'
      || typeof block.text !== 'string'
      || (block.cells !== undefined && (!Array.isArray(block.cells)
        || block.cells.some((cell) => typeof cell?.text !== 'string')))
    ))
  ) throw new Error('Docupedia returned an invalid structured document.');
}

function validateSource(data) {
  if (
    data?.source?.source_type !== 'Docupedia'
    || typeof data.source.id !== 'string'
    || (data.source.original_text !== undefined && typeof data.source.original_text !== 'string')
    || (data.approval_status === 'approved' && typeof data.source.original_text !== 'string')
    || !['pending', 'approved'].includes(data.approval_status)
  ) throw new Error('Docupedia returned an invalid saved document.');
  return data;
}

export async function previewDocupedia(files, signal) {
  if (!files.length || files.length > 20) throw new Error('Select between 1 and 20 documents.');
  const body = new FormData();
  files.forEach((file) => body.append('files', file));
  const data = await request('/preview', { body, signal });
  if (!Array.isArray(data?.results) || data.results.length !== files.length) {
    throw new Error('Docupedia returned an invalid preview result.');
  }
  data.results.forEach((result) => {
    if (typeof result?.filename !== 'string' || !['success', 'error'].includes(result.status)) {
      throw new Error('Docupedia returned an invalid file result.');
    }
    if (result.status === 'success') validateStructured(result.structured_document);
    else if (typeof result.error !== 'string') throw new Error('Docupedia returned an invalid error.');
  });
  return data.results;
}

export async function saveDocupedia(payload, signal) {
  return validateSource(await request('/save', { body: payload, signal }));
}

export async function getDocupedia(sourceId, signal) {
  const data = validateSource(await request(`/${encodeURIComponent(sourceId)}`, {
    method: 'GET', signal,
  }));
  validateStructured(data.structured_document);
  return data;
}

export async function approveDocupedia(sourceId, payload, signal) {
  return validateSource(await request(`/${encodeURIComponent(sourceId)}/approve`, {
    body: payload, signal,
  }));
}
