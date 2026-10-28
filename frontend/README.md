# React + Vite

## Video ingestion

In the Admin Dashboard, choose **Video**, upload one MP4, MOV, or WebM recording,
and wait for local speech transcription. Review and edit the timestamped transcript,
then select **Confirm & Save Video Knowledge**; nothing is saved before confirmation.
You can resume checking an accepted job or discard it after processing. Videos must
contain an audio track and usable speech. Uploads are limited to 500 MB, 20 minutes,
and 3840 x 2160 pixels; codecs depend on FFmpeg. The original video is temporary
and is not stored in the knowledge database.

The backend runs Faster-Whisper locally and uses FFmpeg/FFprobe for audio preparation
and metadata. Only the confirmed transcript is persisted, as a `Video` source in
the shared knowledge database. Saved sources remain editable and searchable by
Employee interview context.

Admin-only video endpoints:

- `POST /api/knowledge/video/process` accepts multipart field `file` and returns
  `202` with a job ID for local processing.
- `GET /api/knowledge/video/jobs/{job_id}` returns job status for polling.
- `GET /api/knowledge/video/jobs/{job_id}/result` returns the transcript preview.
- `POST /api/knowledge/video` accepts JSON `{job_id, title?, text, notes?}` to
  explicitly save the reviewed transcript and returns `{success, source}`.
- `DELETE /api/knowledge/video/jobs/{job_id}` discards an unconfirmed job.

Jobs and previews are held in memory and temporary files, expire after one hour,
and do not survive a backend restart. Run one Uvicorn worker; multiple workers are
unsupported.

### Backend tests and configuration

Session authentication settings in `backend/.env` are required. Audio transcription
uses `GROQ_API_KEY` and the optional `GROQ_PROXY_URL`; video transcription runs
locally and does not use Groq.

Run the automated backend tests from the repository root:

```powershell
Push-Location backend
.\.venv\Scripts\python.exe -m unittest -v test_groq_transport test_audio test_knowledge test_video_routes
Pop-Location
```

Tests use temporary databases and mocked provider/media/extractor calls; they do
not use saved application data or require real video extraction.

## Audio ingestion

In the Admin Dashboard, choose **Audio**, select one MP3, WAV, M4A, or WebM
recording (maximum **25,000,000 bytes**), then choose **Transcribe Audio**.
Upload progress and transcription state are displayed. Review/edit the returned
transcript, optionally set a title and notes, then choose
**Confirm & Save Transcript**. Cancelling before confirmation saves nothing.
Do not re-submit a save if the connection drops without first checking Source
Management: a save may have completed even when its response was lost.

The backend reads `GROQ_API_KEY` from `backend/.env`; it never sends the key to
the browser. Using the existing OpenAI SDK against Groq's compatible API, it
requests `whisper-large-v3-turbo` with a 120-second timeout and no automatic
retries. Recordings are transmitted to Groq; only the confirmed transcript is
stored locally, not the audio. Framework-managed temporary uploads are closed
on success and failure. Uploads are validated by extension, MIME type, size,
and container signature; Groq performs audio decoding.

Admin-only endpoints:

- `POST /api/knowledge/audio/transcribe` accepts multipart field `file` and
  returns `{text, filename}` without writing to SQLite.
- `POST /api/knowledge/audio` accepts JSON `{title?, text, notes?, filename}`
  as the explicit confirmation and returns `{success, source}`.

Confirmed sources use type `Audio` and preserve the original filename separately
from the editable title. They share the existing database, text chunking,
source editing/deletion, and Employee AI Interview context endpoint. The
filename column is added automatically to existing SQLite databases without
changing saved Paste Text records.

For deployment, enforce a multipart request-body limit at the reverse proxy
(allow modest overhead above 25,000,000 bytes), require HTTPS, and allow at least
150 seconds for transcription requests. Endpoint size checks occur after the
framework parses/spools multipart uploads; a proxy limit also bounds ingress.

Install and test from the repository root:

```powershell
.\backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Push-Location backend
.\.venv\Scripts\python.exe -m unittest -v test_audio test_knowledge
Pop-Location
npm --prefix frontend run build
Push-Location frontend
.\node_modules\.bin\eslint.cmd src\components\common\AudioSourceForm.jsx src\pages\AdminDashboard.jsx
Pop-Location
```

The automated backend tests mock Groq (no paid requests or real keys), and use
an isolated temporary SQLite database. They cover format/size validation,
provider failures, cleanup, authorization, confirmation, persistence, metadata,
editing, Employee retrieval, deletion, and existing Paste Text behavior.
The pre-existing `test_transcription.py` is a standalone live OpenAI script;
do not use unrestricted test discovery for this feature.

For a live manual check, run these in separate terminals from the repository root:

```powershell
Push-Location backend
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

```powershell
npm --prefix frontend run dev
```

Open the Vite URL, sign in as Admin, and complete the Audio steps above. Verify
that no source appears before confirmation; after saving, refresh, edit the
Audio source, and query a distinctive transcript term from Employee AI Interview.
Delete the source and verify that the term is no longer returned. This live
check transmits the chosen audio to Groq and may incur API usage charges.

## Saved Paste Text sources

In the Admin Dashboard, **Paste Text** reopens the most recently saved source.
**Source Management > Edit** loads a specific source from the backend by ID,
including its full text and transformation notes. **Update Text** keeps that ID
and replaces its searchable chunks. **Add New Source** opens a blank form;
closing or cancelling without saving does not write changes.

Saved entries have a confirmation-protected **Delete** action. Deletion removes
the source and its chunks permanently; subsequent Employee interview requests
cannot retrieve the deleted text.

The existing bearer session protects all source operations. In addition to text
creation and source listing, the backend exposes Admin-only `GET`, `PUT`, and
`DELETE /api/knowledge/sources/{source_id}` endpoints. The notes column is added
automatically to older SQLite databases; previously unsaved notes cannot be
recovered.

Run backend regression tests from the repository root with:

```powershell
Push-Location backend
.\.venv\Scripts\python.exe -m unittest -v test_knowledge
Pop-Location
```

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend using TypeScript with type-aware lint rules enabled. Check out the [TS template](https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts) for information on how to integrate TypeScript and [`typescript-eslint`](https://typescript-eslint.io) in your project.
