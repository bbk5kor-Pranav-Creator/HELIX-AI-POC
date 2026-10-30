import { useEffect, useRef, useState } from 'react';
import { Trash2, UploadCloud } from 'lucide-react';
import {
  DOCUPEDIA_FILE_LIMIT, DOCUPEDIA_FORMATS, getDocupedia, previewDocupedia,
} from '../../services/docupedia';
import DocupediaReviewCard from './DocupediaReviewCard';
import './docupedia.css';

export default function DocupediaSourceForm({ sourceId, onBusyChange, onSaved, onCancel }) {
  const [files, setFiles] = useState([]);
  const [results, setResults] = useState([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(Boolean(sourceId));
  const [loadVersion, setLoadVersion] = useState(0);
  const request = useRef(null);
  const mutation = useRef(false);
  useEffect(() => () => request.current?.abort(), []);

  useEffect(() => {
    if (!sourceId) return undefined;
    const controller = new AbortController();
    getDocupedia(sourceId, controller.signal).then((data) => {
      if (!controller.signal.aborted) {
        setResults([{
          ...data, id: data.source.id, filename: data.source.filename, status: 'success',
        }]);
      }
    }).catch((failure) => {
      if (!controller.signal.aborted) setError(failure.message || 'Unable to load Docupedia document.');
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [sourceId, loadVersion]);

  const changeBusy = (value) => {
    mutation.current = value;
    setBusy(value);
    onBusyChange(value);
  };
  const selectFiles = (selected) => {
    const incoming = Array.from(selected || []);
    const invalid = incoming.find((file) => (
      !DOCUPEDIA_FORMATS.split(',').includes(`.${file.name.split('.').pop().toLowerCase()}`)
      || file.size === 0 || file.size > DOCUPEDIA_FILE_LIMIT
    ));
    if (invalid) {
      setError(`${invalid.name}: use a non-empty PDF, DOCX, DOC or TXT file up to 50 MiB.`);
      return;
    }
    if (files.length + incoming.length > 20) {
      setError('Select no more than 20 documents per preview.');
      return;
    }
    setFiles((current) => [...current, ...incoming]);
    setError('');
  };
  const preview = async (event) => {
    event.preventDefault();
    if (mutation.current || !files.length) return;
    changeBusy(true);
    setError('');
    request.current = new AbortController();
    try {
      const previews = await previewDocupedia(files, request.current.signal);
      setResults(previews.map((result) => ({ ...result, id: crypto.randomUUID() })));
    } catch (failure) {
      if (failure.name !== 'AbortError') setError(failure.message || 'Unable to extract documents.');
    } finally {
      request.current = null;
      changeBusy(false);
    }
  };
  return (
    <div className="docupedia-source-form">
      {!sourceId && results.length === 0 && (
        <form onSubmit={preview}>
          <p className="admin-source-help">
            PDF, DOCX, DOC and TXT. Maximum 50 MiB per file; up to 20 files.
            Legacy DOC requires LibreOffice on the server. URL ingestion is not supported.
          </p>
          <label className="admin-file-dropzone" htmlFor="docupedia-files">
            <UploadCloud size={27} />
            <strong>Select business documents</strong>
            <input id="docupedia-files" type="file" accept={DOCUPEDIA_FORMATS} multiple disabled={busy}
              onChange={(event) => { selectFiles(event.target.files); event.target.value = ''; }} />
          </label>
          <div className="admin-selected-files">
            {files.map((file, index) => (
              <div key={`${file.name}-${index}`}>
                <span>{file.name} ({(file.size / (1024 * 1024)).toFixed(2)} MiB)</span>
                <button type="button" className="admin-remove-selected-file" disabled={busy}
                  aria-label={`Remove ${file.name}`}
                  onClick={() => setFiles((current) => current.filter((_, position) => position !== index))}>
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
          {busy && <p role="status">Uploading and extracting {files.length} document(s). Please wait...</p>}
          <button type="submit" className="admin-primary-button" disabled={busy || !files.length}>
            {busy ? 'Extracting...' : 'Extract & Preview'}
          </button>
        </form>
      )}
      {loading && <p role="status">Loading structured document...</p>}
      {error && <div className="admin-source-notice error" role="alert">{error}</div>}
      {sourceId && error && (
        <button type="button" className="admin-secondary-button" disabled={busy || loading}
          onClick={() => { setLoading(true); setError(''); setLoadVersion((value) => value + 1); }}>
          Retry Loading
        </button>
      )}
      {results.map((result) => result.status === 'success' ? (
        <DocupediaReviewCard key={result.id} item={result} disabled={busy}
          onBusyChange={changeBusy} onSaved={onSaved} />
      ) : (
        <article className="docupedia-review-card" key={result.id}>
          <strong>{result.filename} — Failed</strong>
          <p className="admin-source-notice error" role="alert">{result.error}</p>
        </article>
      ))}
      <p className="admin-source-modal-note">
        Preview does not save. Save as Pending does not publish. Approve only after reviewing the content.
        Reopen saved Docupedia documents from the repository to resume review.
      </p>
      <div className="admin-source-modal-actions">
        <button type="button" className="admin-secondary-button" disabled={busy} onClick={onCancel}>Close</button>
      </div>
    </div>
  );
}
