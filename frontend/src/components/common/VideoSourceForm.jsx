import { useEffect, useRef, useState } from 'react';
import { processVideo, saveVideo } from '../../services/video';

export default function VideoSourceForm({ onBusyChange, onSaved, onCancel }) {
  const [file, setFile] = useState(null);
  const [title, setTitle] = useState('');
  const [notes, setNotes] = useState('');
  const [preview, setPreview] = useState(null);
  const [text, setText] = useState('');
  const [stage, setStage] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [resumeId, setResumeId] = useState(null);
  const busy = useRef(false);
  const controller = useRef(null);
  const busyState = Boolean(stage);

  useEffect(() => () => controller.current?.abort(), []);

  const selectFile = (event) => {
    if (busy.current) return;
    const selected = event.target.files?.[0];
    setFile(null);
    setPreview(null);
    setResumeId(null);
    setText('');
    setError('');
    if (!selected) return;
    if (!/\.(mp4|mov|webm)$/i.test(selected.name)) {
      setError('Select an MP4, MOV, or WebM video.');
    } else if (!selected.size) {
      setError('Video file is empty.');
    } else if (selected.size > 500000000) {
      setError('Video must be 500 MB (500,000,000 bytes) or smaller.');
    } else {
      setFile(selected);
    }
  };

  const submit = async (event, resume = false) => {
    event.preventDefault();
    if (busy.current || !file || (preview && !resume && !text.trim())) return;
    busy.current = true;
    onBusyChange(true);
    setError('');
    setStage(preview && !resume ? 'saving' : 'uploading');
    setMessage(preview && !resume ? 'Saving approved video knowledge...' : 'Uploading video; completed frame results will be retained when resuming...');
    try {
      if (preview && !resume) {
        const source = await saveVideo({ title, text, notes, filename: preview.filename });
        onSaved(source);
      } else {
        controller.current = new AbortController();
        const result = await processVideo(file, controller.current.signal, (event) => {
          if (event.resume_id) setResumeId(event.resume_id);
          setStage(event.stage);
          setMessage(event.message);
        }, resume ? resumeId : undefined);
        if (result.resume_id) setResumeId(result.resume_id);
        setPreview(result);
        setText(result.text);
      }
    } catch (failure) {
      setError(failure.name === 'AbortError'
        ? 'Processing cancelled. Temporary media will be cleaned after the in-flight operation and bounded retries finish. Completed results can be resumed while this form and checkpoint remain available. Nothing was saved.'
        : failure.message || 'Video processing failed.');
    } finally {
      controller.current = null;
      busy.current = false;
      onBusyChange(false);
      setStage('');
    }
  };

  return (
    <form onSubmit={submit}>
      <label className="admin-source-field-label" htmlFor="video-file">Video recording</label>
      <input id="video-file" className="admin-source-input" type="file" accept=".mp4,.mov,.webm" onChange={selectFile} disabled={busyState} />
      <p className="admin-source-help">One MP4, MOV, or WebM; up to 500 MB, 20 minutes, and 3840 x 2160 pixels. Supported codecs depend on FFmpeg.</p>
      <p className="admin-source-modal-note">Audio and selected frames are sent only to Groq. Frames are sampled across the timeline and scene changes, not continuously; brief details may be missed. Review all extracted claims before saving.</p>
      <label className="admin-source-field-label" htmlFor="video-title">Source Title / Label (Optional)</label>
      <input id="video-title" className="admin-source-input" value={title} onChange={(event) => setTitle(event.target.value)} placeholder={file?.name || 'Video title'} maxLength={120} disabled={busyState} />
      {preview && (
        <>
          <p className="admin-source-help">{preview.filename} · {Math.ceil(preview.duration_seconds)} seconds · {preview.sampled_timestamps?.length || 0} sampled frames</p>
          {preview.coverage && (
            <div className="admin-source-help" role="status">
              <p>Visual coverage: {preview.coverage.visual_coverage === 'selected_samples_complete' ? 'Selected samples completed (not continuous video coverage)' : 'Partial'}.</p>
              <p>Selected: {preview.coverage.selected}; vision analyzed: {preview.coverage.analyzed}; retried frames: {preview.coverage.retried}; retry attempts: {preview.coverage.retry_attempts}; skipped: {preview.coverage.skipped}; duplicates: {preview.coverage.duplicates}; OCR-only: {preview.coverage.ocr_only}.</p>
              <p>First/last successful vision: {preview.coverage.first_analyzed || 'None'} / {preview.coverage.last_analyzed || 'None'}. OCR: {preview.coverage.ocr_status}.</p>
              {preview.coverage.can_resume && preview.coverage.retry_after_seconds > 0 && <p>Provider cooldown at completion: {preview.coverage.retry_after_seconds} seconds. Resume honors the remaining cooldown.</p>}
            </div>
          )}
          {preview.warnings?.length > 0 && (
            <div className="admin-source-notice info" role="status">
              <ul>{preview.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
            </div>
          )}
          <label className="admin-source-field-label" htmlFor="video-knowledge">Review and edit video knowledge</label>
          <textarea id="video-knowledge" className="admin-source-input admin-source-textarea" value={text} onChange={(event) => setText(event.target.value)} rows={12} maxLength={200000} required disabled={busyState} />
        </>
      )}
      <label className="admin-source-field-label" htmlFor="video-notes">Transformation Context / Notes (Optional)</label>
      <textarea id="video-notes" className="admin-source-input admin-source-notes" value={notes} onChange={(event) => setNotes(event.target.value)} rows={3} maxLength={10000} disabled={busyState} />
      <p className="admin-source-modal-note">Nothing is saved until you choose Confirm &amp; Save Video Knowledge. Cancelling discards the preview; the original video is never saved to the knowledge store.</p>
      {busyState && <p role="status" aria-live="polite">{message}</p>}
      {error && <div className="admin-source-notice error" role="alert">{error}</div>}
      <div className="admin-source-modal-actions">
        {busyState && stage !== 'saving' ? (
          <button type="button" className="admin-secondary-button" onClick={() => controller.current?.abort()}>Cancel Processing</button>
        ) : (
          <button type="button" className="admin-secondary-button" onClick={onCancel} disabled={busyState}>Cancel</button>
        )}
        {!busyState && resumeId && (!preview || preview.coverage?.can_resume) && (
          <>
            <button type="button" className="admin-secondary-button" onClick={(event) => {
              if (!preview || window.confirm('Resume replaces this preview with the merged extraction, including your existing speech transcript. Unsaved manual edits in the preview will be replaced. Continue?')) {
                submit(event, true);
              }
            }}>Resume Unfinished Frames</button>
            <button type="button" className="admin-secondary-button" onClick={() => {
              if (!preview || window.confirm('Discard this preview and start a new extraction?')) {
                setResumeId(null);
                setPreview(null);
                setText('');
                setError('');
              }
            }}>Start New Extraction</button>
          </>
        )}
        <button type="submit" className="admin-primary-button" disabled={busyState || !file || (preview && (!text.trim() || preview.has_knowledge === false))}>
          {stage === 'saving' ? 'Saving...' : busyState ? 'Processing...' : preview ? 'Confirm & Save Video Knowledge' : 'Extract Video Knowledge'}
        </button>
      </div>
    </form>
  );
}
