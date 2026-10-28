import { useEffect, useRef, useState } from 'react';
import { discardVideo, getVideoJob, getVideoResult, processVideo, saveVideo } from '../../services/video';

export default function VideoSourceForm({ onBusyChange, onSaved, onCancel }) {
  const [file, setFile] = useState(null);
  const [title, setTitle] = useState('');
  const [notes, setNotes] = useState('');
  const [preview, setPreview] = useState(null);
  const [text, setText] = useState('');
  const [stage, setStage] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [jobId, setJobId] = useState(null);
  const [saved, setSaved] = useState(false);
  const busy = useRef(false);
  const controller = useRef(null);
  const busyState = Boolean(stage);

  useEffect(() => () => controller.current?.abort(), []);

  const selectFile = (event) => {
    if (busy.current) return;
    const selected = event.target.files?.[0];
    setFile(null);
    setPreview(null);
    setJobId(null);
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

  const showStage = (job) => {
    setJobId(job.job_id);
    setStage(job.stage || job.state);
    setMessage(job.message || ({
      queued: 'Video is queued for local processing.',
      processing: 'Transcribing video speech locally.',
      completed: 'Video transcription completed. Review the transcript before saving.',
      failed: 'Video extraction failed. Nothing was saved.',
    })[job.state]);
  };

  const checkJob = async (signal) => {
    while (true) {
      signal.throwIfAborted();
      const job = await getVideoJob(jobId, signal);
      showStage(job);
      if (job.state === 'failed') {
        throw new Error(job.error?.message || 'Video extraction failed. Discard this job before trying another video.');
      }
      if (job.state === 'completed') return getVideoResult(jobId, signal);
      await new Promise((resolve, reject) => {
        const abort = () => {
          clearTimeout(timer);
          signal.removeEventListener('abort', abort);
          reject(signal.reason);
        };
        const timer = setTimeout(() => {
          signal.removeEventListener('abort', abort);
          resolve();
        }, 2000);
        signal.addEventListener('abort', abort, { once: true });
        if (signal.aborted) abort();
      });
    }
  };

  const submit = async (event) => {
    event.preventDefault();
    if (busy.current || saved || (!jobId && !file) || (preview && !text.trim())) return;
    busy.current = true;
    onBusyChange(true);
    setError('');
    setStage(preview ? 'saving' : jobId ? 'processing' : 'uploading');
    setMessage(preview ? 'Saving approved video knowledge...' : jobId
      ? 'Checking the existing video job...' : 'Uploading video for local processing...');
    try {
      if (preview) {
        const source = await saveVideo({ job_id: jobId, title, text, notes });
        setSaved(true);
        onSaved(source);
      } else {
        controller.current = new AbortController();
        const result = jobId
          ? await checkJob(controller.current.signal)
          : await processVideo(file, controller.current.signal, showStage);
        setJobId(result.job_id);
        setPreview(result);
        setText(result.text);
      }
    } catch (failure) {
      setError(failure.name === 'AbortError'
        ? 'Stopped waiting. An accepted job continues on the backend; nothing was saved. Check the existing job again, or discard it once it is no longer processing. If upload acceptance was interrupted, its job ID may be unavailable.'
        : failure.message || 'Video processing failed.');
    } finally {
      controller.current = null;
      busy.current = false;
      onBusyChange(false);
      setStage('');
    }
  };

  const discard = async () => {
    if (busy.current || !jobId || saved) return;
    busy.current = true;
    onBusyChange(true);
    setStage('discarding');
    setMessage('Discarding the unconfirmed video job...');
    setError('');
    try {
      await discardVideo(jobId);
      setJobId(null);
      setPreview(null);
      setText('');
      setMessage('Video job discarded. Nothing was saved.');
    } catch (failure) {
      setError(failure.message || 'Unable to discard the video job. Active extraction cannot be discarded.');
    } finally {
      busy.current = false;
      onBusyChange(false);
      setStage('');
    }
  };

  return (
    <form onSubmit={submit}>
      <label className="admin-source-field-label" htmlFor="video-file">Video recording</label>
      <input id="video-file" className="admin-source-input" type="file" accept=".mp4,.mov,.webm" onChange={selectFile} disabled={busyState || Boolean(jobId) || saved} />
      <p className="admin-source-help">One MP4, MOV, or WebM; up to 500 MB, 20 minutes, and 3840 x 2160 pixels. Supported codecs depend on FFmpeg.</p>
      <p className="admin-source-modal-note">Speech transcription runs locally. Use a video containing clear speech and review the timestamped transcript before saving.</p>
      <label className="admin-source-field-label" htmlFor="video-title">Source Title / Label (Optional)</label>
      <input id="video-title" className="admin-source-input" value={title} onChange={(event) => setTitle(event.target.value)} placeholder={file?.name || 'Video title'} maxLength={120} disabled={busyState} />
      {preview && (
        <>
          <p className="admin-source-help">{preview.filename} · {Math.ceil(preview.metadata.duration_seconds)} seconds</p>
          <label className="admin-source-field-label" htmlFor="video-knowledge">Review and edit video transcript</label>
          <textarea id="video-knowledge" className="admin-source-input admin-source-textarea" value={text} onChange={(event) => setText(event.target.value)} rows={12} maxLength={200000} required disabled={busyState} />
        </>
      )}
      <label className="admin-source-field-label" htmlFor="video-notes">Transformation Context / Notes (Optional)</label>
      <textarea id="video-notes" className="admin-source-input admin-source-notes" value={notes} onChange={(event) => setNotes(event.target.value)} rows={3} maxLength={10000} disabled={busyState} />
      <p className="admin-source-modal-note">Nothing is saved until you choose Confirm &amp; Save Video Knowledge. Closing this form does not discard a backend job. Unconfirmed jobs expire; backend restarts lose them. The original video is never saved to the knowledge store.</p>
      {message && <p role="status" aria-live="polite">{message}</p>}
      {error && <div className="admin-source-notice error" role="alert">{error}</div>}
      <div className="admin-source-modal-actions">
        {busyState && !['saving', 'discarding'].includes(stage) ? (
          <button type="button" className="admin-secondary-button" onClick={() => controller.current?.abort()}>Stop Waiting</button>
        ) : (
          <button type="button" className="admin-secondary-button" onClick={onCancel} disabled={busyState}>Cancel</button>
        )}
        {jobId && !saved && (
          <button type="button" className="admin-secondary-button" onClick={discard} disabled={busyState}>Discard Video Job</button>
        )}
        <button type="submit" className="admin-primary-button" disabled={busyState || saved || (!file && !jobId) || (preview && !text.trim())}>
          {saved ? 'Saved' : stage === 'saving' ? 'Saving...' : stage === 'discarding' ? 'Discarding...' : busyState ? 'Processing...' : preview ? 'Confirm & Save Video Knowledge' : jobId ? 'Check Video Job' : 'Extract Video Knowledge'}
        </button>
      </div>
    </form>
  );
}
