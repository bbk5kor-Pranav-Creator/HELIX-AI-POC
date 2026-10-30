import { useEffect, useRef, useState } from 'react';
import {
  approveDocupedia, DOCUPEDIA_APPROVAL_LIMIT, getDocupedia, saveDocupedia,
} from '../../services/docupedia';
import DocupediaDocumentView from './DocupediaDocumentView';

export default function DocupediaReviewCard({ item, disabled, onBusyChange, onSaved }) {
  const [document, setDocument] = useState(item.structured_document);
  const [source, setSource] = useState(item.source || null);
  const [text, setText] = useState(
    item.approval_status === 'approved' ? item.source.original_text
      : item.structured_document.reviewed_text ?? item.structured_document.original_text,
  );
  const [status, setStatus] = useState(item.approval_status || 'preview');
  const [error, setError] = useState('');
  const [operation, setOperation] = useState('');
  const [locked, setLocked] = useState(false);
  const [confirmApproval, setConfirmApproval] = useState(false);
  const request = useRef(null);
  const busy = useRef(false);
  const saveAttempt = useRef(null);
  const approvalAttempt = useRef(null);
  const editorId = `docupedia-reviewed-${item.id}`;
  useEffect(() => () => request.current?.abort(), []);

  const run = async (label, action) => {
    if (busy.current || disabled) return;
    busy.current = true;
    setOperation(label);
    setError('');
    onBusyChange(true);
    request.current = new AbortController();
    try {
      await action(request.current.signal);
    } catch (failure) {
      if (failure.name !== 'AbortError') {
        setError(failure.message || 'Docupedia request failed.');
        if ([413, 422].includes(failure.status)) {
          saveAttempt.current = null;
          approvalAttempt.current = null;
          setLocked(false);
          setConfirmApproval(false);
        }
      }
    } finally {
      request.current = null;
      busy.current = false;
      setOperation('');
      onBusyChange(false);
    }
  };

  const save = () => run('Saving pending document...', async (signal) => {
    saveAttempt.current ??= {
      filename: item.filename,
      structured_document: { ...document, reviewed_text: text },
      idempotency_key: crypto.randomUUID(),
    };
    setLocked(true);
    const data = await saveDocupedia(saveAttempt.current, signal);
    setSource(data.source);
    setStatus(data.approval_status);
    if (data.approval_status === 'approved') setText(data.source.original_text);
    setLocked(false);
    onSaved(data.source);
  });

  const approve = () => run('Approving reviewed content...', async (signal) => {
    approvalAttempt.current ??= { text, idempotency_key: crypto.randomUUID() };
    setLocked(true);
    const data = await approveDocupedia(source.id, approvalAttempt.current, signal);
    setSource(data.source);
    setStatus(data.approval_status);
    setText(data.source.original_text);
    setConfirmApproval(false);
    setLocked(false);
    onSaved(data.source);
  });

  const reload = () => run('Loading saved document...', async (signal) => {
    const data = await getDocupedia(source.id, signal);
    setDocument(data.structured_document);
    setSource(data.source);
    setStatus(data.approval_status);
    setText(data.approval_status === 'approved'
      ? data.source.original_text
      : data.structured_document.reviewed_text ?? data.structured_document.original_text);
    approvalAttempt.current = null;
    setLocked(false);
    setConfirmApproval(false);
    onSaved(data.source);
  });

  const count = Array.from(text).length;
  const validText = text.trim() && count <= DOCUPEDIA_APPROVAL_LIMIT;
  return (
    <article className="docupedia-review-card">
      <div className="docupedia-card-heading">
        <strong>{source?.title || item.filename}</strong>
        <span className={`admin-preview-status ${error ? 'error' : ''}`} role="status">
          {error ? 'Failed' : status === 'approved' ? 'Approved' : status === 'pending' ? 'Pending Approval' : 'Preview'}
        </span>
      </div>
      {source && <p className="admin-source-help">Document ID: {source.id}</p>}
      {document.warnings.length > 0 && (
        <div className="admin-source-notice info">
          <strong>Extraction warnings</strong>
          <ul>{document.warnings.map((warning, index) => (
            <li key={index}>{warning.message || warning.code}
              {warning.page_number != null ? ` (page ${warning.page_number})` : ''}
            </li>
          ))}</ul>
        </div>
      )}
      <details open>
        <summary>Original document structure (read-only)</summary>
        <DocupediaDocumentView document={document} />
      </details>
      <details>
        <summary>Raw extracted text (preserved)</summary>
        <pre className="docupedia-raw">{document.original_text}</pre>
      </details>
      <label className="admin-source-field-label" htmlFor={editorId}>Reviewed content for approval</label>
      <textarea
        id={editorId}
        className="admin-source-textarea docupedia-editor"
        value={text}
        rows={12}
        disabled={disabled || locked || status === 'approved'}
        onChange={(event) => { setText(event.target.value); setConfirmApproval(false); }}
      />
      <p className="admin-source-help">
        {count.toLocaleString()} / {DOCUPEDIA_APPROVAL_LIMIT.toLocaleString()} characters.
        Editing never changes the raw extraction. Only explicit approval publishes this text.
      </p>
      {count > DOCUPEDIA_APPROVAL_LIMIT && (
        <p className="admin-source-notice error" role="alert">
          The approval API accepts at most 200,000 characters. Nothing has been truncated.
        </p>
      )}
      {operation && <p className="admin-source-notice info" role="status">{operation}</p>}
      {error && <p className="admin-source-notice error" role="alert">{error}</p>}
      {locked && !operation && (
        <p className="admin-source-help">
          The previous request may have completed. Retry the identical request
          {source ? ' or reload the saved document' : ''} before changing its content.
        </p>
      )}
      <div className="docupedia-actions">
        {!source && (
          <button type="button" className="admin-primary-button" disabled={disabled || !text.trim()} onClick={save}>
            {locked ? 'Retry Pending Save' : 'Save as Pending'}
          </button>
        )}
        {source && (
          <button type="button" className="admin-secondary-button" disabled={disabled} onClick={reload}>
            Reload Saved Document
          </button>
        )}
        {status === 'pending' && (
          <button
            type="button"
            className="admin-primary-button"
            disabled={disabled || !validText}
            onClick={() => locked ? approve() : setConfirmApproval(true)}
          >
            {locked ? 'Retry Approval' : 'Approve Reviewed Content'}
          </button>
        )}
      </div>
      {confirmApproval && status === 'pending' && !locked && (
        <div className="admin-source-notice info">
          <p>Publish this reviewed content to Employee knowledge search? This document can only be approved once.</p>
          <div className="docupedia-actions">
            <button type="button" className="admin-primary-button" disabled={disabled} onClick={approve}>Confirm Approval</button>
            <button type="button" className="admin-secondary-button" disabled={disabled} onClick={() => setConfirmApproval(false)}>Keep Pending</button>
          </div>
        </div>
      )}
      {status === 'pending' && !locked && (
        <p className="admin-source-help">
          The draft saved with the pending document can be reopened later. Any further edits here
          are submitted only on approval; reloading discards those unsaved changes.
        </p>
      )}
    </article>
  );
}
