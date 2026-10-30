import { useEffect, useRef, useState } from 'react';
import { previewPublicLink, savePublicLink } from '../../services/publicLink';

export default function PublicLinkSourceForm({ onBusyChange, onSaved, onCancel }) {
  const [url, setUrl] = useState('');
  const [title, setTitle] = useState('');
  const [preview, setPreview] = useState(null);
  const [generatedContent, setGeneratedContent] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const request = useRef(null);
  const busy = useRef(false);

  useEffect(() => () => request.current?.abort(), []);

  const submit = async (event) => {
    event.preventDefault();
    if (busy.current) return;

    const trimmedUrl = url.trim();
    let parsedUrl;
    try {
      parsedUrl = new URL(trimmedUrl);
    } catch {
      setError('Enter a valid public HTTP or HTTPS URL.');
      return;
    }
    if (!['http:', 'https:'].includes(parsedUrl.protocol) || !parsedUrl.hostname) {
      setError('Enter a valid public HTTP or HTTPS URL.');
      return;
    }

    busy.current = true;
    onBusyChange(true);
    setIsLoading(true);
    setError('');
    request.current = new AbortController();
    try {
      const result = await previewPublicLink(
        { url: trimmedUrl, title },
        request.current.signal,
      );
      setPreview(result);
      setGeneratedContent(result.generated_content);
    } catch (failure) {
      if (failure.name !== 'AbortError') {
        setError(failure.message || 'Unable to generate a Public Link preview. Please retry.');
      }
    } finally {
      request.current = null;
      busy.current = false;
      setIsLoading(false);
      onBusyChange(false);
    }
  };

  const save = async () => {
    if (busy.current || !preview || !generatedContent.trim()) return;

    busy.current = true;
    onBusyChange(true);
    setIsLoading(true);
    setError('');
    request.current = new AbortController();
    try {
      const source = await savePublicLink({
        url: preview.source_url || preview.url,
        title: title.trim() || preview.title,
        text: generatedContent,
        metadata: preview.metadata,
      }, request.current.signal);
      onSaved(source);
    } catch (failure) {
      if (failure.name !== 'AbortError') {
        setError(failure.message || 'Unable to save Public Link knowledge. Please retry.');
      }
    } finally {
      request.current = null;
      busy.current = false;
      setIsLoading(false);
      onBusyChange(false);
    }
  };

  const sourceUrl = preview?.source_url || preview?.url;
  const pageTitle = preview?.metadata?.page_title || preview?.title;

  return (
    <form onSubmit={submit}>
      <label className="admin-source-field-label" htmlFor="public-link-url">
        Public webpage URL
      </label>
      <input
        id="public-link-url"
        className="admin-source-input"
        type="url"
        value={url}
        onChange={(event) => {
          setUrl(event.target.value);
          setPreview(null);
          setGeneratedContent('');
          setError('');
        }}
        placeholder="https://example.com/process-document"
        required
        disabled={isLoading}
      />
      <label className="admin-source-field-label" htmlFor="public-link-title">
        Source Title / Label (Optional)
      </label>
      <input
        id="public-link-title"
        className="admin-source-input"
        type="text"
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        placeholder="Use the page title"
        maxLength={120}
        disabled={isLoading}
      />
      <p className="admin-source-help">
        Gemini reads the supplied public page and drafts a structured explanation of its actual content.
        Review every claim: generated text may omit details or contain errors. Previewing does not save it.
      </p>

      {isLoading && (
        <div className="admin-source-notice info" role="status" aria-live="polite">
          Preparing source content for review...
        </div>
      )}
      {error && <div className="admin-source-notice error" role="alert">{error}</div>}

      {preview && (
        <section className="public-link-preview" aria-label="Public Link preview">
          <div className="public-link-preview-details">
            <strong>{pageTitle}</strong>
            <a href={sourceUrl} target="_blank" rel="noreferrer">
              {sourceUrl}
            </a>
          </div>
          <label className="admin-source-field-label" htmlFor="public-link-generated-content">
            Review and edit generated knowledge
          </label>
          <textarea
            id="public-link-generated-content"
            className="admin-source-input admin-source-textarea public-link-preview-content"
            value={generatedContent}
            onChange={(event) => setGeneratedContent(event.target.value)}
            rows={12}
            maxLength={200000}
            disabled={isLoading}
          />
          <p className="admin-source-help">
            This AI-generated document remains an unsaved preview in the source language.
          </p>
        </section>
      )}

      <p className="admin-source-modal-note">
        Only the reviewed content above is saved. The source URL and extraction metadata are
        retained separately.
      </p>
      <div className="admin-source-modal-actions">
        <button type="button" className="admin-secondary-button" onClick={onCancel} disabled={isLoading}>
          Cancel
        </button>
        <button type="submit" className="admin-secondary-button" disabled={isLoading || !url.trim()}>
          {isLoading ? 'Processing...' : error && !preview ? 'Retry Preview' : preview ? 'Regenerate Preview' : 'Generate Preview'}
        </button>
        <button
          type="button"
          className="admin-primary-button"
          onClick={save}
          disabled={isLoading || !preview || !generatedContent.trim()}
        >
          {isLoading && preview ? 'Saving...' : 'Save'}
        </button>
      </div>
    </form>
  );
}
