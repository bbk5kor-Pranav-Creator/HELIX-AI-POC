import { useRef, useState } from 'react';
import { getWorkspaceSession } from '../../utils/auth';

const MAX_AUDIO_BYTES = 25000000;

function uploadAudio(file, token, onProgress) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', '/api/knowledge/audio/transcribe');
    request.setRequestHeader('Authorization', `Bearer ${token}`);
    request.responseType = 'json';
    request.timeout = 150000;
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100));
    };
    request.onload = () => {
      const data = request.response;
      if (request.status >= 200 && request.status < 300 && typeof data?.text === 'string') {
        resolve(data);
      } else {
        reject(new Error(typeof data?.detail === 'string' ? data.detail : 'Unable to transcribe audio.'));
      }
    };
    request.onerror = () => reject(new Error('Audio upload failed. Check your connection.'));
    request.ontimeout = () => reject(new Error('Transcription timed out. Please retry.'));
    const body = new FormData();
    body.append('file', file);
    request.send(body);
  });
}

export default function AudioSourceForm({ onBusyChange, onSaved, onCancel }) {
  const [file, setFile] = useState(null);
  const [filename, setFilename] = useState('');
  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  const [notes, setNotes] = useState('');
  const [hasPreview, setHasPreview] = useState(false);
  const [phase, setPhase] = useState('');
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const busy = useRef(false);

  const selectFile = (event) => {
    if (busy.current) return;
    const selected = event.target.files?.[0];
    setFile(null);
    setFilename('');
    setText('');
    setHasPreview(false);
    setError('');
    if (!selected) return;
    if (!/\.(mp3|wav|m4a|webm)$/i.test(selected.name)) {
      setError('Select an MP3, WAV, M4A, or WebM file.');
    } else if (!selected.size) {
      setError('Audio file is empty.');
    } else if (selected.size > MAX_AUDIO_BYTES) {
      setError('Audio must be 25 MB or smaller.');
    } else {
      setFile(selected);
    }
  };

  const submit = async (event) => {
    event.preventDefault();
    if (busy.current || !file || (hasPreview && !text.trim())) return;
    busy.current = true;
    onBusyChange(true);
    setError('');
    setPhase(hasPreview ? 'saving' : 'uploading');
    setProgress(0);
    try {
      const session = getWorkspaceSession();
      if (!session?.accessToken || session.role !== 'Admin') {
        throw new Error('Your Admin session is missing or expired. Please sign in again.');
      }
      if (!hasPreview) {
        const result = await uploadAudio(file, session.accessToken, (value) => {
          setProgress(value);
          if (value === 100) setPhase('transcribing');
        });
        setText(result.text);
        setFilename(result.filename);
        setHasPreview(true);
      } else {
        const response = await fetch('/api/knowledge/audio', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${session.accessToken}`,
          },
          body: JSON.stringify({ title: title.trim() || undefined, text, notes, filename }),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(typeof data.detail === 'string' ? data.detail : 'Unable to save the transcript.');
        }
        onSaved(data.source);
      }
    } catch (failure) {
      setError(failure.message || 'Audio processing failed.');
    } finally {
      busy.current = false;
      onBusyChange(false);
      setPhase('');
    }
  };

  return (
    <form onSubmit={submit}>
      <label className="admin-source-field-label" htmlFor="audio-file">Recording</label>
      <input id="audio-file" className="admin-source-input" type="file" accept=".mp3,.wav,.m4a,.webm" onChange={selectFile} disabled={Boolean(phase)} />
      <p className="admin-source-help">MP3, WAV, M4A, WebM. One file, up to 25 MB (25,000,000 bytes). Audio is sent to Groq for transcription.</p>
      <label className="admin-source-field-label" htmlFor="audio-title">Source Title / Label (Optional)</label>
      <input id="audio-title" className="admin-source-input" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={120} placeholder={file?.name || 'Recording title'} disabled={Boolean(phase)} />
      {hasPreview && (
        <>
          <label className="admin-source-field-label" htmlFor="audio-transcript">Review and edit transcript</label>
          <textarea id="audio-transcript" className="admin-source-input admin-source-textarea" value={text} onChange={(event) => setText(event.target.value)} rows={9} maxLength={200000} required disabled={Boolean(phase)} />
        </>
      )}
      <label className="admin-source-field-label" htmlFor="audio-notes">Transformation Context / Notes (Optional)</label>
      <textarea id="audio-notes" className="admin-source-input admin-source-notes" value={notes} onChange={(event) => setNotes(event.target.value)} rows={3} maxLength={10000} disabled={Boolean(phase)} />
      <p className="admin-source-modal-note">Nothing is saved to the knowledge store until you confirm the transcript. Cancelling discards the preview.</p>
      {phase && (
        <div role="status" aria-live="polite">
          {phase === 'saving' ? 'Saving transcript...' : phase === 'transcribing' ? 'Upload complete. Transcribing audio...' : `Uploading audio: ${progress}%`}
          {phase === 'uploading' && <progress max="100" value={progress} aria-label="Audio upload progress" />}
        </div>
      )}
      {error && <div className="admin-source-notice error" role="alert">{error}</div>}
      <div className="admin-source-modal-actions">
        <button type="button" className="admin-secondary-button" onClick={onCancel} disabled={Boolean(phase)}>Cancel</button>
        <button type="submit" className="admin-primary-button" disabled={Boolean(phase) || !file || (hasPreview && !text.trim())}>
          {phase ? 'Processing...' : hasPreview ? 'Confirm & Save Transcript' : 'Transcribe Audio'}
        </button>
      </div>
    </form>
  );
}
