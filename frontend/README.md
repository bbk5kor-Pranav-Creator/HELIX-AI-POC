# React + Vite

## Video ingestion

Choose **Video** in the Admin Dashboard, upload a recording, and select
**Extract Video Knowledge**. The UI streams actual stages from the backend:
validation, extracting audio (or no-audio detection), transcription, frame
analysis, and preparation. Review/edit the preview and select
**Confirm & Save Video Knowledge**. No knowledge source is written until
confirmation. **Cancel Processing** aborts the stream; an in-flight tool/API
operation (including its bounded retries/waits) is allowed to finish before cleanup.
A retry may report that the processing slot is occupied while cleanup completes.
**Resume Unfinished Frames** reuploads the same file and reuses successful
speech/vision results, not a saved video. Resuming replaces the editable preview;
the UI asks before overwriting manual edits. An expired checkpoint can be reset
with **Start New Extraction**. Nothing is saved to the knowledge store until approval.

### Dependencies and environment

No additional Python/npm packages are required beyond the Audio dependencies.
Install **FFmpeg and FFprobe** locally, with the required input codecs available.
Tesseract is optional for local OCR; without it, the vision model extracts
visible text and the preview explicitly warns that local OCR was unavailable.
This workstation has FFmpeg/FFprobe 9.0.2 installed through WinGet, with absolute
paths persisted in the ignored backend environment file. Tesseract is not
installed. Keep the approved Px proxy running and restart the backend after
configuration changes; no global proxy environment settings were changed.
On Windows, installation with WinGet (subject to organizational policy):

```powershell
winget install --id Gyan.FFmpeg --exact
# Optional local OCR:
winget install --id tesseract-ocr.tesseract --exact --source winget
```

Restart the terminal/backend after changing PATH, then verify:

```powershell
ffmpeg -version
ffprobe -version
tesseract --version
```

The current Tesseract installer has no current-user WinGet variant on this
workstation; normal installation may require an administrator-approved prompt.
After installation, add this to `backend/.env` if Tesseract is not on PATH:

```dotenv
TESSERACT_PATH='C:\Program Files\Tesseract-OCR\tesseract.exe'
```

Verify that `tesseract --list-langs` lists `eng`. Restart the backend. Missing
executables, timeout, or missing language data are explicit OCR warnings, not a
claim that OCR succeeded; vision still runs. English is the default OCR language.

Backend configuration is read from `backend/.env`:

| Variable | Purpose |
| --- | --- |
| `GROQ_API_KEY` | Existing key, used only server-side for speech and vision. |
| `GROQ_PROXY_URL` | Optional Groq-only HTTP(S) proxy. On this workstation, the approved Px proxy is `http://127.0.0.1:3128`; keep Px running using the existing root `Proxy.bat`. This does not change proxy settings for other app requests. |
| `GROQ_VISION_MODEL` | Required for diagram/screen interpretation: use a currently available Groq **image-input** model ID accessible to your account. There is deliberately no guessed/hardcoded default. |
| `GROQ_VIDEO_REQUEST_INTERVAL_SECONDS` | Optional minimum interval between vision requests, 1-120 seconds; default 4. Actual provider cooldowns and token reserve can require longer waits. Also used between video speech segments; Audio behavior is unchanged. |
| `GROQ_VIDEO_MAX_RETRIES` | Additional attempts for transient vision errors/429, default 3, range 0-5. SDK automatic retries remain disabled. |
| `GROQ_VIDEO_RETRY_WAIT_SECONDS` | Per-frame cumulative sleep budget, default 120 seconds, range 0-300. A longer provider cooldown returns a partial result rather than retrying early. Each individual API attempt also has a 120-second timeout. |
| `FFMPEG_PATH` / `FFPROBE_PATH` | Optional absolute executable paths if the binaries are not on PATH, e.g. `C:\Tools\ffmpeg\bin\ffmpeg.exe`. |
| `TESSERACT_PATH` | Optional absolute Tesseract executable path. |

Before selecting a vision model, check the current
[Groq vision documentation](https://console.groq.com/docs/vision),
[speech documentation](https://console.groq.com/docs/speech-to-text), and
[account rate limits](https://console.groq.com/settings/limits).
During workstation setup, `qwen/qwen3.8-27b` was listed by this account and
successfully accepted a synthetic image-input request through the approved Px
proxy. It is configured in the local ignored environment file; availability is
account-dependent and may change. A later synthetic-image request returned
`x-ratelimit-limit-tokens: 8000`, remaining tokens, and a token reset duration,
plus a request-limit header of `1000`. These are runtime observations, not
guaranteed account limits or inferred quota-window definitions. Documentation
access returned HTTP 403. The code checks
the configured model against Groq's accessible model list at runtime; that list
alone does not prove image support. Each image request must also succeed.
An absent/inaccessible/non-image model or rate-limit failure produces an explicit
error/warning; the system never claims unavailable vision analysis succeeded.
No API keys or raw provider error bodies are returned to the browser.

### Processing and limits

- MP4, MOV, WebM; maximum **500,000,000 bytes**, **20 minutes**, and
  **3840 x 2160 pixels**. Unsupported codecs/corrupt inputs are rejected.
- Uploads are copied in 1 MiB blocks to a private temporary directory. The
  framework may spool multipart data before route validation: set a reverse-proxy
  body limit slightly above 500 MB and require HTTPS.
- FFmpeg restricts input to local MOV/MP4/Matroska/WebM containers, extracts
  16 kHz mono FLAC in approximately 180-second ordered segments, and uses the
  same `whisper-large-v3-turbo` helper as Audio. Each segment is checked against
  the existing 25 MB upload limit. No automatic transcription retries.
- Segments with peak volume at or below -50 dB are skipped with a warning.
  Non-speech music/noise can still produce erroneous transcripts; review them.
  Segment timestamps are approximate ranges, not word-level alignment.
  Audio extraction/transcription failures do not prevent visual-only results.
- At most 24 regularly spaced frames (target 15-second spacing until the cap),
  one near the end, and up to 12 additional scene changes are sampled (at most
  **37 frames**, roughly at most 50 seconds between regular samples for 20 minutes).
  Scene detection scans the entire video at 2 fps with a 0.10 scene threshold.
  When many changes occur, 12 changes are distributed across the complete detected
  timeline, not the first 12 changes. This is still not exhaustive coverage.
- Decoded 320x180 grayscale thumbnails identify identical/very-near-identical
  screens before any vision requests. The conservative comparison retains small
  UI changes, but can still miss very tiny changes; duplicate samples are
  explicitly counted as skipped, never as separately vision-analyzed.
- Selected JPEGs have a maximum dimension of 1600 and are checked to be at most
  4 MB. Vision uses one inline base64 `image_url` per request, sequentially,
  with bounded output and a 120-second request timeout. The start, end, then
  progressively filled timeline gaps are interpreted first to avoid spending all
  available quota at the beginning. Findings are presented chronologically.
  The prompt prioritizes app/screen purpose, exact UI labels, visible instructions,
  state changes, decision points, and diagram edges; previous observations and
  speech are explicitly context rather than proof of unseen actions. Check these payload
  settings against the configured model's current limits.
- OCR and vision are separately labelled. Diagram edges and screen/demo
  observations are kept distinct from explicitly uncertain inferred relationships.
  Repeated extracted findings are replaced by timestamped references; OCR lines
  already present in the vision description are omitted, retaining other labels.
  No second model invents or fills missing transitions.
- Vision HTTP 429, 408/409, connection failures/timeouts, and selected 5xx errors
  receive exponential backoff with jitter. `retry-after` (seconds or HTTP date),
  `retry-after-ms`, and exhausted request/token reset headers take precedence.
  Successful response token usage plus remaining/reset headers also reserve
  capacity for the next image/output budget instead of relying on a fixed delay.
  No quota bypass or unlimited retries. A long cooldown is retained for Resume.
  When a frame exhausts its budget, further vision calls stop for that pass to
  avoid hammering the shared quota. Completed results remain available.
- Speech or OCR failures are explicit; speech is never discarded because a visual
  sample is uninformative. Usable speech or OCR can be reviewed as
  a **partial result** without vision; OCR alone does not interpret diagrams.
  If no useful content exists, the partial preview explains this and confirmation
  is disabled; unfinished vision can still be resumed.
- One active video per backend process, sequential vision calls, bounded tool
  timeouts, no durable job queue or video storage. Run one Uvicorn worker for a
  single shared processing slot; multiple workers multiply this concurrency.
  Temporary video/audio/frames are removed after success, failure, or disconnect
  cleanup. Process crashes/forced OS termination are outside that guarantee.
- Resume checkpoints contain only extracted text, per-frame results/thumbnail
  comparison bytes, and cooldowns in this worker's memory: no original media,
  new database, or durable queue. At most 4 checkpoints are retained; one-hour
  idle expiry is checked lazily on the next upload, or data disappears on process
  restart/eviction. They are bound to the authenticated Admin identity, file SHA-256,
  and configured model. The frontend retains the opaque ID only while the form
  is open. Use one worker (or sticky routing); another worker/restart can return
  HTTP 410. Reuploading a different file or changing model returns HTTP 409.
- Proxies must disable response buffering for
  `/api/knowledge/video/process` and allow long-running streamed requests.
  Stage lines serve as progress, not a fabricated percentage.

Admin-only endpoints:

- `POST /api/knowledge/video/process`: multipart `file` and optional `resume_id`,
  returns an initial `checkpoint` event with `resume_id`, then newline-delimited
  JSON stages. Terminal stage is `complete` with
  `{text, filename, duration_seconds, sampled_timestamps, coverage, resume_id,
  has_knowledge, warnings}`, or `error`
  with `message`. Once streaming starts, errors are terminal events rather than
  new HTTP statuses.
- `coverage` distinguishes selected, successfully vision-analyzed (including
  inspected-but-uninformative frames), retried frames, retry attempts, skipped
  samples (including duplicates), duplicates, OCR-only frames, first/last vision
  success, successful/unfinished timestamps, OCR status, cooldown and resume
  possibility, safe provider limit headers, and reasons for incompleteness.
  `selected_samples_complete` means all unique selected frames succeeded;
  `full_video_coverage` is always false because sampling cannot prove continuous
  coverage. `sampled_timestamps` alone must not be interpreted as successful analysis.
  Retry counts are cumulative actual repeated vision calls, including Resume,
  not scheduled waits that ended before another call.
- `POST /api/knowledge/video`: explicit JSON confirmation
  `{title?, text, notes?, filename}`; returns `{success, source}`.

Confirmed entries use source type `Video` and the existing filename/title columns.
Duration, timestamps, extraction coverage, and warnings live in the editable
knowledge text, so no schema change is required. The shared SQLite chunking,
source Edit/Delete actions, dashboard counts/filters, and Employee AI Interview
retrieval all use the same pipeline. If a save response is lost, check Source
Management before retrying; duplicate clicks are prevented, but arbitrary replay
of an independently issued confirmation is not server-idempotent.

### Video tests and manual verification

The reliability changes passed 59 focused backend tests (no skips), the frontend
production build, and focused ESLint checks. Browser checks with mocked streams
covered partial coverage, resume IDs, preview replacement, explicit-only save,
duplicate submission prevention, metadata-only save blocking, and expired
checkpoints. A live 36-second synthetic UI video using the configured Groq model
produced Google AI Studio/Create prompt/System instructions/Run labels at all four
selected timestamps from 00:00:00 to 00:00:35. OCR was unavailable and reported
accurately. No source was saved; temporary media were removed. The original
reported 2:33 recording was not available for reprocessing.

From the repository root:

```powershell
Push-Location backend
.\.venv\Scripts\python.exe -m unittest -v test_video_reliability test_groq_transport test_video test_video_media test_audio test_knowledge
Pop-Location
npm --prefix frontend run build
Push-Location frontend
.\node_modules\.bin\eslint.cmd src\components\common\VideoSourceForm.jsx src\services\video.js src\components\common\AudioSourceForm.jsx src\pages\AdminDashboard.jsx
Pop-Location
```

Tests use temporary databases/files and mocked Groq responses. The real media
smoke test generates tiny synthetic silent/audio videos and runs only when both
FFmpeg and FFprobe are installed; otherwise it explicitly skips.
That smoke test also reads only `FFMPEG_PATH` and `FFPROBE_PATH` from the local
backend environment file when the calling terminal does not have them set.

Workstation setup verification completed on 2026-10-23:

- All 38 focused backend tests passed, including the real FFmpeg smoke test
  and Groq-only proxy tests; no skipped tests.
- Frontend production build and focused ESLint checks passed.
- Live Groq vision identified a synthetic image and extracted process labels
  from a generated silent tutorial video.
- An authenticated API check processed a generated video with locally synthesized
  speech, obtained both transcript and visual knowledge, confirmed no premature
  save, then verified confirmation, Employee retrieval, and deletion.
- Live checks used only synthetic media and an isolated temporary database.
  Temporary media/database files and the temporary API server were cleaned up;
  the production knowledge database was not used.

Start the backend and frontend in separate terminals using the commands in the
Audio section below. Verify a spoken recording and a silent diagram/tutorial:
watch stages, check timestamped visual observations and partial warnings, edit
the preview, cancel once without saving, then reprocess and confirm. Refresh
Source Management, filter Video, edit the saved entry, and start an Employee AI
Interview to retrieve its process statements. Delete the entry and verify its
chunks are no longer returned. Live analysis sends extracted audio/frames to
Groq and may incur normal Groq usage charges.

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
