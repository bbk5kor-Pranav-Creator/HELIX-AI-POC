import { useEffect, useRef, useState } from 'react';
import { FileText, Trash2, UploadCloud, X } from 'lucide-react';
import { previewDocuments, saveDocuments } from '../../services/documents';

const ACCEPTED_FORMATS = '.pdf,.docx,.doc,.xlsx,.xls,.pptx,.ppt,.txt';

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MiB`;
}

function createIdempotencyKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (character) => {
    const random = Math.floor(Math.random() * 16);
    return (character === 'x' ? random : (random & 0x3) | 0x8).toString(16);
  });
}

export default function DocsSourceForm({ onBusyChange, onSaved, onCancel }) {
  const [files, setFiles] = useState([]);
  const [results, setResults] = useState([]);
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const busy = useRef(false);
  const request = useRef(null);
  const nextId = useRef(0);

  useEffect(() => () => request.current?.abort(), []);

  const addFiles = (fileList) => {
    const newFiles = Array.from(fileList || []).map((file) => ({
      id: nextId.current++,
      file,
    }));
    if (!newFiles.length || busy.current) return;

    setFiles((current) => [...current, ...newFiles]);
    setResults([]);
    setError('');
  };

  const removeFile = (fileId) => {
    if (busy.current) return;
    setFiles((current) => current.filter((item) => item.id !== fileId));
    setResults([]);
    setError('');
  };

  const editResult = (index, text) => {
    setResults((current) => current.map((result, resultIndex) => (
      resultIndex === index
        ? { ...result, text, saveStatus: 'idle', saveError: '' }
        : result
    )));
  };

  const submit = async (event) => {
    event.preventDefault();
    if (busy.current) return;
    if (!files.length) {
      setError('Select at least one document to extract.');
      return;
    }

    busy.current = true;
    onBusyChange(true);
    setIsLoading(true);
    setResults([]);
    setError('');
    request.current = new AbortController();
    try {
      const previews = await previewDocuments(
        files.map(({ file }) => file),
        request.current.signal,
      );
      setResults(previews.map((result) => ({
        ...result,
        idempotencyKey: createIdempotencyKey(),
        saveStatus: 'idle',
        saveError: '',
      })));
    } catch (failure) {
      if (failure.name !== 'AbortError') {
        setError(failure.message || 'Unable to extract the selected documents.');
      }
    } finally {
      request.current = null;
      busy.current = false;
      setIsLoading(false);
      onBusyChange(false);
    }
  };

  const saveResult = async (index) => {
    if (busy.current) return;
    const result = results[index];
    if (
      !result
      || result.status !== 'success'
      || result.saveStatus === 'saved'
      || !result.text.trim()
    ) return;

    busy.current = true;
    onBusyChange(true);
    setIsLoading(true);
    setResults((current) => current.map((item, resultIndex) => (
      resultIndex === index
        ? { ...item, saveStatus: 'saving', saveError: '' }
        : item
    )));
    request.current = new AbortController();
    try {
      const [savedResult] = await saveDocuments([{
        filename: result.filename,
        text: result.text,
        metadata: result.metadata,
        idempotency_key: result.idempotencyKey,
      }], request.current.signal);
      if (savedResult.status === 'error') {
        throw new Error(savedResult.error);
      }

      setResults((current) => current.map((item, resultIndex) => (
        resultIndex === index
          ? { ...item, saveStatus: 'saved', saveError: '' }
          : item
      )));
      onSaved?.(savedResult.source);
    } catch (failure) {
      if (failure.name !== 'AbortError') {
        setResults((current) => current.map((item, resultIndex) => (
          resultIndex === index
            ? {
              ...item,
              saveStatus: 'error',
              saveError: failure.message || 'Unable to save this document.',
            }
            : item
        )));
      }
    } finally {
      request.current = null;
      busy.current = false;
      setIsLoading(false);
      onBusyChange(false);
    }
  };

  return (
    <form onSubmit={submit}>
      <label className="admin-source-field-label" htmlFor="docs-files">
        Upload documents
      </label>
      <p className="admin-source-help">
        PDF, DOCX, DOC, XLSX, XLS, PPTX, PPT, or TXT. Maximum 50 MiB per file.
      </p>
      <label
        className={`admin-file-dropzone ${isDragging ? 'is-dragging' : ''}`}
        htmlFor="docs-files"
        onDragOver={(event) => {
          event.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setIsDragging(false);
          addFiles(event.dataTransfer.files);
        }}
      >
        <UploadCloud size={27} />
        <strong>Click to browse or drag and drop</strong>
        <span>Multiple files supported · 50 MiB maximum per file</span>
        <input
          id="docs-files"
          type="file"
          accept={ACCEPTED_FORMATS}
          multiple
          disabled={isLoading}
          onChange={(event) => {
            addFiles(event.target.files);
            event.target.value = '';
          }}
        />
      </label>

      {files.length > 0 ? (
        <div className="admin-selected-files" aria-label="Selected documents">
          {files.map(({ id, file }) => (
            <div key={id}>
              <FileText size={15} />
              <span>{file.name}</span>
              <span className="admin-file-size">{formatFileSize(file.size)}</span>
              <button
                type="button"
                className="admin-remove-selected-file"
                aria-label={`Remove ${file.name}`}
                title={`Remove ${file.name}`}
                disabled={isLoading}
                onClick={() => removeFile(id)}
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      ) : (
        <p className="admin-source-help" role="status">
          No documents selected.
        </p>
      )}

      {isLoading && (
        <div className="admin-source-notice info" role="status" aria-live="polite">
          Extracting {files.length} {files.length === 1 ? 'document' : 'documents'}...
        </div>
      )}
      {error && <div className="admin-source-notice error" role="alert">{error}</div>}

      {results.length > 0 && (
        <section className="docs-extraction-results" aria-label="Document extraction results">
          <h3>Extraction preview</h3>
          {results.map((result, index) => (
            <article className="docs-extraction-result" key={`${result.filename}-${index}`}>
              <div className="docs-extraction-result-heading">
                <strong>{result.filename}</strong>
                <span className={`admin-preview-status ${result.status === 'error' || result.saveStatus === 'error' ? 'error' : ''}`}>
                  {result.status === 'error'
                    ? 'Failed'
                    : result.saveStatus === 'saved' ? 'Saved' : 'Extracted'}
                </span>
              </div>
              {result.status === 'error' ? (
                <div className="admin-source-notice error" role="alert">
                  <X size={15} />
                  <span>{result.error}</span>
                </div>
              ) : (
                <>
                  <p className="admin-source-help">
                    {result.metadata?.format?.toUpperCase()} ·{' '}
                    {formatFileSize(result.metadata?.size_bytes || 0)} ·{' '}
                    {result.metadata?.text_characters} extracted characters
                  </p>
                  <label
                    className="admin-source-field-label"
                    htmlFor={`docs-preview-${index}`}
                  >
                    Review and edit extracted content
                  </label>
                  <textarea
                    id={`docs-preview-${index}`}
                    className="admin-source-input admin-source-textarea docs-preview-content"
                    value={result.text}
                    onChange={(event) => editResult(index, event.target.value)}
                    rows={10}
                    disabled={isLoading || result.saveStatus === 'saved'}
                  />
                  {result.saveError && (
                    <div className="admin-source-notice error" role="alert">
                      <X size={15} />
                      <span>{result.saveError}</span>
                    </div>
                  )}
                  <div className="docs-save-actions">
                    <button
                      type="button"
                      className="admin-primary-button"
                      onClick={() => saveResult(index)}
                      disabled={isLoading || result.saveStatus === 'saved' || !result.text.trim()}
                    >
                      {result.saveStatus === 'saving'
                        ? 'Saving...'
                        : result.saveStatus === 'saved' ? 'Saved to Knowledge' : 'Save to Knowledge'}
                    </button>
                  </div>
                </>
              )}
            </article>
          ))}
        </section>
      )}

      <p className="admin-source-modal-note">
        Documents are saved only when you choose Save to Knowledge. Reviewed text is stored in the knowledge repository and split into searchable chunks.
      </p>
      <div className="admin-source-modal-actions">
        <button
          type="button"
          className="admin-secondary-button"
          onClick={onCancel}
          disabled={isLoading}
        >
          Cancel
        </button>
        <button
          type="submit"
          className="admin-primary-button"
          disabled={isLoading || files.length === 0}
        >
          {isLoading
            ? 'Processing...'
            : results.length ? 'Run Extraction Again' : 'Extract & Preview'}
        </button>
      </div>
    </form>
  );
}
