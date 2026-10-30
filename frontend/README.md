# React + Vite

## Docupedia business-document review

The Admin Docupedia tile supports direct PDF, DOCX, DOC and TXT uploads
(50 MiB per file, up to 20 files). Legacy DOC extraction requires backend
LibreOffice. Docupedia URL ingestion is not supported.

1. **Extract & Preview** calls `POST /api/knowledge/docupedia/preview`.
   Headings, paragraphs, tables, available provenance and extraction warnings are
   displayed without persisting anything.
2. Edit **Reviewed content for approval**. The original blocks and raw extraction
   remain read-only and unchanged.
3. **Save as Pending** calls `POST /api/knowledge/docupedia/save`. The unchanged
   extraction is submitted together with a `reviewed_text` draft in the existing
   extensible structured payload. This draft is preserved in extraction metadata;
   it does not replace `original_text`, source blocks, or approved canonical text.
4. Open **Repository → Docupedia → Review** to retrieve a pending or approved
   document through `GET /api/knowledge/docupedia/{source_id}`. Pending drafts
   are restored. Further edits after the initial pending save are local until
   approval; reloading discards those unsaved changes.
5. **Approve Reviewed Content → Confirm Approval** calls
   `POST /api/knowledge/docupedia/{source_id}/approve`. Only this action publishes
   the reviewed text to Employee search. Approved documents are read-only.

Approval has the backend's 200,000-character limit; oversized content is reported,
not truncated. Preview/save have a 16 MiB structured-JSON response/payload policy.
The interface retains request IDs and exact payloads when retrying uncertain
saves/approvals, disables concurrent actions, and displays HTTP 409 conflicts with
a reload action for saved documents. Known validation failures allow correction.
All requests use the existing Admin bearer session. No backend or database changes
are required for this frontend integration.

## Public Link preview (Gemini URL Context)

The active Public Link preview uses `backend/gemini_public_link_routes.py`.
Firecrawl preview processing is paused: `backend/main.py` registers the Gemini
router instead of the legacy Firecrawl router. The legacy module remains for
its shared URL-security helpers, request/save models, save handler, and regression
tests; no Firecrawl fallback or Groq generation is used by the active preview.

Configure `GEMINI_API_KEY` on the backend. No key is sent to the frontend.
Provider rejections are classified into safe, actionable configuration errors
(invalid key, disabled API, permissions, unsupported region, unavailable model,
or unsupported request settings). Quota/rate-limit failures return HTTP 429.
Raw provider error messages and credentials are not logged or returned.

For an organization-approved TLS inspection proxy, optionally set the backend
environment variable `PUBLIC_LINK_CA_BUNDLE` to the **absolute path of a readable
PEM CA certificate bundle supplied and approved by your organization**. Obtain
the correct root/intermediate CA certificates from your infrastructure team;
do not use a website's leaf certificate or an unverified downloaded certificate.
Configure the path separately and restart the backend. No certificate or default
organization-specific path is shipped with the application.

Public Link redirect probes create a normal default TLS context first, then add
the configured CA bundle with `load_verify_locations`. Default public/system
trust, required certificate verification, hostname checks, validated-IP
connections, and SNI remain enabled. The bundle is used for direct HTTPS probes,
HTTPS proxy connections, and destination TLS inside proxy tunnels. An unreadable,
invalid, or relative bundle path fails closed with HTTP 503. Certificate failures
return a specific HTTP 502; logs include only the probe route, error category and
numeric TLS verification code, never certificates, bundle paths, or credentials.
This setting is scoped to the redirect probes: it does not change Gemini's
provider transport, shared Groq transport, or proxy DNS fallback trust.

The model is `gemini-3.5-flash`, using the Gemini REST API's URL Context tool
(`{"url_context": {}}`, equivalent to the SDK's `Tool(url_context=UrlContext())`).
The existing REST transport is reused; no additional SDK dependency is required.
Each preview validates the submitted public URL and observed redirects, then
makes at most one generation request (no automatic retry, search, or batching).
The prompt requests a clear, structured, source-language explanation of the
page's actual content, suitable for a standalone knowledge document,
containing only supported purpose, core concepts, activities, rules, and technical
details. Short sources should produce short documents. Generated claims still
require Admin review; successful URL retrieval does not prove factual fidelity.

Generation uses temperature 0.1, one candidate, model-default thinking, and a
4,096-token output ceiling. No Gemini 2.5-specific thinking-budget override is
sent. Blocked/incomplete outputs, failed/missing URL Context
retrieval metadata, and unexpected retrieved URLs are rejected. The generated
document must fit the existing 200,000-character preview/save limit. The HTTP
response has a 2 MB transport ceiling and a 90-second timeout.

**Provider retrieval limitations:** Gemini retrieves the public page itself.
Backend validation cannot enforce every provider-side redirect, inspect original
page bytes, enforce the legacy raw-source size limit, or guarantee whole-page
coverage. The provider may use cached content. JavaScript/login-protected pages
may not work. The tool's fetched context tokens are not bounded locally: only the
prompt and output allowance are bounded. No source text is fabricated to fill
missing retrieval evidence.

The frontend request/response field names and editable preview/save flow remain
the same. `generated_content` contains the generated document; `cleaned_content`
is empty because URL Context does not return the original page text. Metadata
records provider, model, retrieval status, and `source_text_available=false`.
Raw/cleaned extraction sizes and extraction chunk counts are omitted rather
than invented. `generation_metrics` reports one call, zero locally available
source characters, submitted prompt characters, latency, and provider token
usage when present; estimated tokens remain null. Total usage may include tool
and thinking tokens, not just visible output. Logs contain numeric
metrics/error categories only, never prompts, keys, or document text.

Only explicit Admin Save persists the reviewed/edited document using the
existing Public Link save handler and SQLite knowledge store. No schema or
Employee retrieval changes are required.

Mocked regression tests (no provider calls):

```powershell
Push-Location backend
.\.venv\Scripts\python.exe -B -m unittest test_public_link test_groq_transport test_gemini_public_link
Pop-Location
```

### Legacy Firecrawl pipeline (paused)

The following describes the retained, inactive Firecrawl preview implementation,
not the active Gemini preview.

In the Admin Dashboard, choose **Public Link**, enter a public HTTP or HTTPS URL,
and select **Generate Preview**. The Admin-only
`POST /api/knowledge/public-link/preview` endpoint accepts `{url, title?}` and
returns the page title, source URL, editable `generated_content`, `cleaned_content`,
and metadata. Short cleaned pages (up to 6,000 characters) and longer pages whose
lines are all at most 1,000 characters go directly to review with **zero Groq calls**.
The source is not expanded into a report or translated.

Public Link makes **zero Groq formatting calls**, including for dense text.
Python soft-wraps only unambiguous plain prose at existing spaces, targeting
1,000-character lines without splitting words. It preserves all words, order,
punctuation, numbers, and total character count. Short pages and readable pages
remain unchanged. Pages with Markdown structure, code, tables, lists, links,
URLs, or ambiguous syntax retain the cleaned source exactly; new list structures
are not introduced by wrapping. There is no summarization or semantic rewriting.

The former 18,500-character dense-generation limit and token-budget checks no
longer apply because there is no generation request. Structured pages with long
code/table lines can go directly to review up to the overall limits: 800,000 raw
Markdown characters and 200,000 cleaned characters. Exceeding these limits still
returns an actionable error without truncation. `GROQ_API_KEY`, `GROQ_TEXT_MODEL`,
and `GROQ_TEXT_MAX_COMPLETION_TOKENS` are not read for Public Link processing.
The existing proxy helper remains in use for URL security probes/DNS fallback.

`metadata.generation_metrics` reports `call_count`, `source_chars`,
`submitted_chars`, `estimated_input_tokens`,
`input_token_budget`, `max_completion_tokens`, provider-reported
`actual_input_tokens`, `actual_output_tokens`, `actual_total_tokens`,
`usage_status`, `latency_ms`, and `outcome`. These fields remain for compatibility:
new previews always report zero calls, zero submitted characters, zero generation
latency, null token estimates/budgets/usage, and `usage_status="not_requested"`.
Source characters still measure the cleaned extraction. Numeric metrics and
fixed processing outcomes are logged, never source text or credentials.

Current outcomes are `direct`, `structured_source`, `source_preserved` (no safe
wrapping opportunity), and `deterministic_formatted` (plain prose soft-wrapped
locally). Historical generation metrics remain readable/savable, but no new
preview reports historical generation outcomes. `generation_batch_count` is
always zero; `chunk_count` still describes extraction chunks, not stored retrieval
chunks. The result remains an editable, unsaved Admin preview, not automatic
approval. Metadata uses the existing JSON storage without database schema changes.

Previewing does not save to the knowledge database. Review/edit the text and select
**Save** to submit `{url, title?, text, metadata}` to the Admin-only
`POST /api/knowledge/public-link` endpoint. Only reviewed text is stored as a
`Public Link` source, using the existing SQLite chunking and Employee retrieval.
Firecrawl options, URL security, and authentication are unchanged.

Firecrawl still requests only Markdown with `only_main_content=True` and no
automatic retries; HTML/raw HTML is not used as a fallback. Python removes
explicit navigation/footer/script/style blocks, exact UI controls, link-only
boilerplate sections, and exact repeated sections/prose. It preserves source
order, link/image destinations, paragraphs, headings, lists, tables, code
indentation, numbers, and non-Latin text. Substantive documentation is not
discarded merely because its heading says "Navigation" or "Cookie settings".
Structural cleaning also removes closed HTML navigation/banner/content-info
roles and select controls (outside protected code), leading root-linked local
menus (compact rows or repeated UI) and their exact repetitions, and link-only
outlines whose labels and local anchors match subsequent headings in source order. Numbered link lists
are retained because their ordering may encode instructions, including under
boilerplate-like headings. Quoted link rows are not treated as outlines or menus. Multi-link rows
are recognized without site-specific labels. Unmatched outlines, ordinary reference collections,
article headers, and substantive sections remain for Admin review; ambiguous
Markdown is not classified as boilerplate merely because it contains many links.

Main-content cleaning additionally removes closed HTML forms, input/button/
textarea controls, and address elements outside code examples. Recognizable
international/formatted phone numbers, explicitly labelled phone fields and
postal-address fields, and number-plus-street-name patterns are removed or
marked as removed in prose (also in code when recognizable contact details
occur there). Generic technical `Address:` fields are not treated as postal
addresses. This is heuristic removal, not guaranteed multilingual contact-data
anonymization: unlabeled addresses, locality-only text, and unusual phone
formats may remain for Admin review.

Long lines are not discarded solely because of their length. Lines longer than
1,000 characters are removed only when they combine bundle/source-map markers,
at least 20 semicolons/braces, and less than 10% whitespace. This targets
recognizable minified assets, including those in Markdown fences, without
deleting ordinary long prose, JSON data, or technical examples indiscriminately.
There is no linked-page crawling, summarization, or additional provider call.

Extraction metadata includes `raw_content_chars`, `content_chars` (cleaned size),
and `useful_content_chars` (non-whitespace characters in non-heading body lines,
excluding Markdown fences/separators; this is a size measure, not a relevance
score). Empty/bodyless results, recognized access/error pages, and provider
metadata HTTP errors are rejected before formatting. A result below 200 characters
that retains less than 5% of the raw Markdown is also rejected for manual review
rather than claiming a potentially incomplete extraction is complete. Logs
contain sizes/categories, not page text. These new metadata fields use the
existing metadata JSON; there is no database schema change.

Run the mocked Public Link and transport tests (no live provider requests):

```powershell
Push-Location backend
.\.venv\Scripts\python.exe -m unittest -v test_public_link test_groq_transport
Pop-Location
```

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
