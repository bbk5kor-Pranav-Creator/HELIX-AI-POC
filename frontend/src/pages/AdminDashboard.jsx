import { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  ArrowLeft,
  ArrowUpRight,
  AudioLines,
  BookOpen,
  CheckCircle2,
  FileText,
  FolderOpen,
  Grid2X2,
  Link as LinkIcon,
  Mail,
  Network,
  Search,
  Settings,
  Sparkles,
  Trash2,
  UploadCloud,
  Video,
  X,
} from 'lucide-react';
import EmployeeProfileMenu from '../components/common/EmployeeProfileMenu';
import './admin-dashboard.css';

const SOURCE_CATEGORIES = [
  {
    id: 'docupedia',
    title: 'Docupedia',
    category: 'KNOWLEDGE REPOSITORY',
    description: 'Access and prepare enterprise process documentation from Docupedia.',
    icon: BookOpen,
    type: 'blue',
    inputLabel: 'Docupedia URL',
    placeholder: 'https://docupedia.bosch.com/wiki/...',
    inputType: 'docupedia',
    accept: '.pdf,.docx,.doc,.txt',
    multiple: false,
  },
  {
    id: 'sharepoint',
    title: 'SharePoint',
    category: 'DOCUMENT REPOSITORY',
    description: 'Prepare SharePoint site references and organizational documents.',
    icon: FolderOpen,
    type: 'purple',
    inputLabel: 'SharePoint site URL',
    placeholder: 'https://company.sharepoint.com/...',
    inputType: 'url',
  },
  {
    id: 'jira',
    title: 'Jira',
    category: 'PROJECT MANAGEMENT',
    description: 'Add project issues, tasks, and change-record references.',
    icon: Activity,
    type: 'blue',
    inputLabel: 'Jira project or board URL',
    placeholder: 'https://company.atlassian.net/...',
    inputType: 'url',
  },
  {
    id: 'signavio',
    title: 'Signavio',
    category: 'PROCESS MODELS',
    description: 'Prepare references to business process models and documentation.',
    icon: Network,
    type: 'green',
    inputLabel: 'Signavio workspace URL',
    placeholder: 'https://... Enter the workspace URL',
    inputType: 'url',
  },
  {
    id: 'outlook',
    title: 'Outlook',
    category: 'EMAIL & COMMUNICATION',
    description: 'Describe the email content or mailbox export to prepare for ingestion.',
    icon: Mail,
    type: 'purple',
    inputLabel: 'Email source details',
    placeholder: 'Summary of process emails and transformation notices...',
    inputType: 'outlook',
    accept: '.msg,.eml,.pdf,.txt',
    multiple: true,
  },
  {
    id: 'docs',
    title: 'Docs',
    category: 'DOCUMENT UPLOAD',
    description: 'Select documents for a future process-analysis workflow.',
    icon: FileText,
    type: 'blue',
    inputLabel: 'Upload Documents',
    inputType: 'file',
    accept: '.pdf,.docx,.doc,.xls,.xlsx,.ppt,.pptx,.txt',
    multiple: true,
  },
  {
    id: 'audio',
    title: 'Audio',
    category: 'AUDIO INGESTION',
    description: 'Select recordings for future transcription and analysis.',
    icon: AudioLines,
    type: 'green',
    inputLabel: 'Select audio files',
    inputType: 'file',
    accept: 'audio/*',
    multiple: true,
  },
  {
    id: 'video',
    title: 'Video',
    category: 'VIDEO INGESTION',
    description: 'Select video material for future processing.',
    icon: Video,
    type: 'purple',
    inputLabel: 'Select video files',
    inputType: 'file',
    accept: 'video/*',
    multiple: true,
  },
  {
    id: 'link',
    title: 'Link',
    category: 'WEB RESOURCE',
    description: 'Add a URL pointing to a relevant business-process resource.',
    icon: LinkIcon,
    type: 'blue',
    inputLabel: 'Resource URL',
    placeholder: 'https://example.com/process-document',
    inputType: 'url',
  },
  {
    id: 'paste-text',
    title: 'Paste Text',
    category: 'TEXT INPUT',
    description: 'Add process notes or business documentation as text.',
    icon: FileText,
    type: 'green',
    inputLabel: 'Process documentation',
    placeholder: 'Paste your process documentation here...',
    inputType: 'textarea',
  },
];

const formatDate = (value) => {
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(value));
  } catch {
    return 'Date unavailable';
  }
};

const isValidHttpUrl = (value) => {
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
};

function AdminDashboard() {
  const [activeView, setActiveView] = useState('overview');
  const [selectedSource, setSelectedSource] = useState(null);
  const [sourceTitle, setSourceTitle] = useState('');
  const [sourceValue, setSourceValue] = useState('');
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [docupediaMode, setDocupediaMode] = useState('upload');
  const [outlookMode, setOutlookMode] = useState('upload');
  const [isDraggingFiles, setIsDraggingFiles] = useState(false);
  const [sourceNotes, setSourceNotes] = useState('');
  const [notice, setNotice] = useState('');
  const [noticeType, setNoticeType] = useState('info');
  const [repositorySearch, setRepositorySearch] = useState('');
  const [repositoryFilter, setRepositoryFilter] = useState('all');
  const [sources, setSources] = useState([]);

  const totalSourcesCount = sources.length;

  const filteredSources = useMemo(() => {
    const query = repositorySearch.trim().toLowerCase();
    return sources.filter((source) => {
      const matchesFilter = repositoryFilter === 'all' || source.type === repositoryFilter;
      const searchableText = [
        source.title,
        source.categoryName,
        source.value,
        source.notes,
        ...(source.files || []).map((file) => file.name),
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return matchesFilter && (!query || searchableText.includes(query));
    });
  }, [sources, repositorySearch, repositoryFilter]);

  useEffect(() => {
    if (!selectedSource) return undefined;

    const handleKeyDown = (event) => {
      if (event.key === 'Escape') setSelectedSource(null);
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [selectedSource]);

  const openSource = (source) => {
    setSelectedSource(source);
    setSourceTitle('');
    setSourceValue('');
    setSelectedFiles([]);
    setDocupediaMode('upload');
    setOutlookMode('upload');
    setIsDraggingFiles(false);
    setSourceNotes('');
    setNotice('');
    setNoticeType('info');
  };

  const closeSource = () => {
    setSelectedSource(null);
    setSourceTitle('');
    setSourceValue('');
    setSelectedFiles([]);
    setDocupediaMode('upload');
    setOutlookMode('upload');
    setIsDraggingFiles(false);
    setSourceNotes('');
    setNotice('');
    setNoticeType('info');
  };

  const openView = (view) => {
    setActiveView(view);
    setSelectedSource(null);
    setNotice('');
  };

  const removeSource = (sourceId) => {
    setSources((currentSources) => currentSources.filter((source) => source.id !== sourceId));
  };

  const handleSourceSubmit = (event) => {
    event.preventDefault();
    if (!selectedSource) return;

    const trimmedValue = sourceValue.trim();
    const isDocupedia = selectedSource.id === 'docupedia';
    const isOutlook = selectedSource.id === 'outlook';
    const isFileSource = selectedSource.inputType === 'file';
    const needsFiles = (isDocupedia && docupediaMode === 'upload') ||
      (isOutlook && outlookMode === 'upload') ||
      (isFileSource && !isOutlook);
    const needsUrl = (isDocupedia && docupediaMode === 'url') ||
      (selectedSource.inputType === 'url' && !isDocupedia);
    const needsText = (isOutlook && outlookMode === 'info') || selectedSource.id === 'paste-text';

    if (needsFiles && selectedFiles.length === 0) {
      setNotice(isDocupedia
        ? 'Please select a Docupedia document to add.'
        : isOutlook
          ? 'Please select an exported email file.'
          : `Please select at least one ${selectedSource.title} file.`);
      setNoticeType('error');
      return;
    }

    if (needsUrl && (!trimmedValue || !isValidHttpUrl(trimmedValue))) {
      setNotice(`Please enter a valid ${selectedSource.title} URL beginning with http:// or https://.`);
      setNoticeType('error');
      return;
    }

    if (needsText && !trimmedValue) {
      setNotice(isOutlook
        ? 'Please enter correspondence details or switch to Upload Exported Email.'
        : 'Please enter some process documentation before continuing.');
      setNoticeType('error');
      return;
    }

    const oversizedFile = selectedFiles.find((file) => file.size > 50 * 1024 * 1024);
    if (oversizedFile) {
      setNotice(`${oversizedFile.name} exceeds the 50 MB per-file limit.`);
      setNoticeType('error');
      return;
    }

    const fileNames = selectedFiles.map((file) => ({
      name: file.name,
      size: file.size,
      type: file.type,
    }));
    const recordValue = needsUrl || (isOutlook && outlookMode === 'info') || selectedSource.id === 'paste-text'
      ? trimmedValue
      : '';
    const inferredTitle = selectedFiles[0]?.name ||
      (recordValue && isDocupedia ? recordValue.replace(/^https?:\/\//, '') : '') ||
      `${selectedSource.title} Source`;

    const record = {
      id: `${selectedSource.id}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      type: selectedSource.id,
      categoryName: selectedSource.title,
      categoryLabel: selectedSource.category,
      title: sourceTitle.trim() || inferredTitle,
      value: recordValue,
      notes: sourceNotes.trim(),
      files: fileNames,
      inputMode: isDocupedia ? docupediaMode : isOutlook ? outlookMode : undefined,
      createdAt: new Date().toISOString(),
      status: 'Preview record',
    };

    setSources((currentSources) => [record, ...currentSources]);
    setNotice('Source record added to this session. No file has been uploaded and no external connection has been made.');
    setNoticeType('success');
    setSelectedSource(null);
    setActiveView('repository');
    setRepositorySearch('');
    setRepositoryFilter('all');
    setSourceTitle('');
    setSourceValue('');
    setSelectedFiles([]);
    setDocupediaMode('upload');
    setOutlookMode('upload');
    setIsDraggingFiles(false);
    setSourceNotes('');
  };

  const handleFiles = (fileList) => {
    const files = Array.from(fileList || []);
    const oversizedFile = files.find((file) => file.size > 50 * 1024 * 1024);
    if (oversizedFile) {
      setNotice(`${oversizedFile.name} exceeds the 50 MB per-file limit.`);
      setNoticeType('error');
      return;
    }
    const acceptedFiles = selectedSource?.multiple === false ? files.slice(0, 1) : files;
    setSelectedFiles(acceptedFiles);
    setNotice('');
    setNoticeType('info');
  };

  const renderOverview = () => (
    <>
      <div className="studio-heading admin-heading">
        <div className="studio-label">
          <Sparkles size={15} />
          PROCESS GOVERNANCE
        </div>

        <div className="admin-title-row">
          <div>
            <h2>Source Ingestion</h2>
            <p>
              Bring enterprise process knowledge together by preparing documentation
              and source references for future ingestion.
            </p>
          </div>

          <div className="admin-heading-actions">
            <button
              type="button"
              className="admin-secondary-button"
              onClick={() => openView('repository')}
            >
              <FolderOpen size={16} />
              Repository ({totalSourcesCount})
            </button>
            <button
              type="button"
              className="admin-primary-button"
              onClick={() => openView('analytics')}
            >
              <Activity size={16} />
              Analytics
            </button>
          </div>
        </div>
      </div>

      <section className="admin-channel-section">
        <div className="admin-section-heading">
          <h3>Available Channels (10)</h3>
          <span>Select a channel to prepare a source record</span>
        </div>

        <div className="admin-source-grid">
          {SOURCE_CATEGORIES.map((source) => {
            const Icon = source.icon;
            return (
              <button
                type="button"
                className="admin-source-card"
                key={source.id}
                onClick={() => openSource(source)}
              >
                <div className="admin-source-card-top">
                  <span className={`admin-source-icon ${source.type}`}>
                    <Icon size={21} />
                  </span>
                  <ArrowUpRight size={17} className="admin-source-arrow" />
                </div>
                <span className="admin-source-category">{source.category}</span>
                <strong>{source.title}</strong>
                <span className="admin-source-description">{source.description}</span>
              </button>
            );
          })}
        </div>
      </section>

      {totalSourcesCount === 0 ? (
        <section className="admin-empty-state">
          <div className="admin-empty-icon"><FolderOpen size={20} /></div>
          <div>
            <strong>No source records yet</strong>
            <p>
              Choose one of the ten channels above to prepare a source record.
              Records created here are session-only previews until backend ingestion is connected.
            </p>
          </div>
        </section>
      ) : (
        <section className="admin-recent-section">
          <div className="admin-recent-heading">
            <div>
              <h3>Recent Source Records ({totalSourcesCount})</h3>
              <p>Preview records created during this session.</p>
            </div>
            <button type="button" className="admin-text-button" onClick={() => openView('repository')}>
              View repository <ArrowUpRight size={15} />
            </button>
          </div>
          <div className="admin-recent-list">
            {sources.slice(0, 4).map((source) => {
              const category = SOURCE_CATEGORIES.find((item) => item.id === source.type);
              const Icon = category?.icon || FileText;
              return (
                <article className="admin-recent-item" key={source.id}>
                  <span className={`admin-source-icon ${category?.type || 'blue'}`}><Icon size={18} /></span>
                  <div className="admin-recent-item-copy">
                    <strong>{source.title}</strong>
                    <span>{source.categoryName} · {formatDate(source.createdAt)}</span>
                  </div>
                  <span className="admin-preview-status">Preview</span>
                </article>
              );
            })}
          </div>
        </section>
      )}
    </>
  );

  const renderRepository = () => (
    <section className="admin-subview admin-repository-view">
      <div className="studio-label"><FolderOpen size={15} /> KNOWLEDGE REPOSITORY</div>
      <div className="admin-subview-title-row">
        <div>
          <h2>Repository</h2>
          <p>Review source records prepared during this session. These entries are not yet uploaded to a backend.</p>
        </div>
        <button type="button" className="admin-primary-button" onClick={() => openView('overview')}>
          <ArrowLeft size={16} /> Source Ingestion
        </button>
      </div>

      <div className="admin-repository-toolbar">
        <label className="admin-repository-search">
          <Search size={17} />
          <input
            type="search"
            value={repositorySearch}
            onChange={(event) => setRepositorySearch(event.target.value)}
            placeholder="Search by source name, title, URL, or file..."
            aria-label="Search repository records"
          />
        </label>
        <label className="admin-repository-filter">
          <span>Source type</span>
          <select value={repositoryFilter} onChange={(event) => setRepositoryFilter(event.target.value)}>
            <option value="all">All types</option>
            {SOURCE_CATEGORIES.map((category) => (
              <option key={category.id} value={category.id}>{category.title}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="admin-repository-summary">
        <strong>{filteredSources.length} {filteredSources.length === 1 ? 'record' : 'records'}</strong>
        <span>of {totalSourcesCount} session {totalSourcesCount === 1 ? 'record' : 'records'}</span>
      </div>

      {filteredSources.length === 0 ? (
        <div className="admin-empty-state admin-repository-empty">
          <div className="admin-empty-icon"><FolderOpen size={20} /></div>
          <div>
            <strong>{totalSourcesCount === 0 ? 'No repository records yet' : 'No matching records'}</strong>
            <p>
              {totalSourcesCount === 0
                ? 'Prepare a source from Source Ingestion and it will appear here for this session.'
                : 'Try another search term or change the source-type filter.'}
            </p>
            {totalSourcesCount === 0 && (
              <button type="button" className="admin-text-button" onClick={() => openView('overview')}>
                Browse source channels <ArrowUpRight size={15} />
              </button>
            )}
          </div>
        </div>
      ) : (
        <div className="admin-repository-list">
          {filteredSources.map((source) => {
            const category = SOURCE_CATEGORIES.find((item) => item.id === source.type);
            const Icon = category?.icon || FileText;
            return (
              <article className="admin-repository-item" key={source.id}>
                <span className={`admin-source-icon ${category?.type || 'blue'}`}><Icon size={20} /></span>
                <div className="admin-repository-item-main">
                  <div className="admin-repository-item-title-row">
                    <strong>{source.title}</strong>
                    <span className="admin-preview-status">Session preview</span>
                  </div>
                  <span className="admin-repository-category">{source.categoryName} · {source.categoryLabel}{source.inputMode ? ` · ${source.inputMode === 'upload' ? 'File upload mode' : source.inputMode === 'url' ? 'URL mode' : 'Reference details mode'}` : ''}</span>
                  {source.value && <p className="admin-repository-value">{source.value}</p>}
                  {source.files?.length > 0 && (
                    <div className="admin-repository-files">
                      {source.files.map((file, index) => (
                        <span key={`${source.id}-${file.name}-${index}`}><FileText size={13} />{file.name}</span>
                      ))}
                    </div>
                  )}
                  {source.notes && <p className="admin-repository-notes">Notes: {source.notes}</p>}
                  <span className="admin-repository-date">Added {formatDate(source.createdAt)}</span>
                </div>
                <button
                  type="button"
                  className="admin-remove-source"
                  onClick={() => removeSource(source.id)}
                  aria-label={`Remove ${source.title} preview record`}
                  title="Remove preview record"
                >
                  <Trash2 size={16} />
                </button>
              </article>
            );
          })}
        </div>
      )}
      <p className="admin-repository-disclaimer">
        Preview mode: records are held in React state only and will be cleared when this page is refreshed.
      </p>
    </section>
  );

  const renderAnalytics = () => {
    const sourceTypeCount = new Set(sources.map((source) => source.type)).size;
    const fileCount = sources.reduce((count, source) => count + (source.files?.length || 0), 0);
    const textCount = sources.filter((source) => Boolean(source.value)).length;

    return (
      <section className="admin-subview">
        <div className="studio-label"><Activity size={15} /> ADMIN ANALYTICS</div>
        <div className="admin-subview-title-row">
          <div>
            <h2>Analytics</h2>
            <p>Session-only counts based on the source preview records currently held in this page.</p>
          </div>
          <button type="button" className="admin-secondary-button" onClick={() => openView('overview')}>
            <ArrowLeft size={16} /> Back to Source Ingestion
          </button>
        </div>

        <div className="admin-analytics-grid">
          <article className="admin-analytics-card">
            <span>Source Records</span>
            <strong>{totalSourcesCount}</strong>
            <p>Preview records created in this session.</p>
          </article>
          <article className="admin-analytics-card">
            <span>Source Types Used</span>
            <strong>{sourceTypeCount}</strong>
            <p>Distinct channel types represented by current records.</p>
          </article>
          <article className="admin-analytics-card">
            <span>Selected Files</span>
            <strong>{fileCount}</strong>
            <p>File names registered in preview records; files are not uploaded.</p>
          </article>
          <article className="admin-analytics-card">
            <span>Text or URL Inputs</span>
            <strong>{textCount}</strong>
            <p>Records that include a text or URL value.</p>
          </article>
        </div>
        <div className="admin-analytics-note">
          <CheckCircle2 size={18} />
          <p>These are local UI counts, not live telemetry, ingestion status, or backend processing metrics.</p>
        </div>
      </section>
    );
  };

  const renderSettings = () => (
    <section className="admin-subview">
      <div className="studio-label"><Settings size={15} /> ADMINISTRATION</div>
      <h2>Settings</h2>
      <p>Administrative configuration will be added when the relevant requirements and backend services are available.</p>
      <div className="admin-empty-state">
        <div className="admin-empty-icon"><Settings size={20} /></div>
        <div>
          <strong>Settings are not configured</strong>
          <p>No configuration has been changed or saved.</p>
        </div>
      </div>
      <button type="button" className="admin-primary-button" onClick={() => openView('overview')}>
        <ArrowLeft size={16} /> Back to Source Ingestion
      </button>
    </section>
  );

  return (
    <div className="employee-app admin-app">
      <header className="login-header employee-topbar">
        <div className="header-brand topbar-brand">
          <img src="/bosch-logo.png" alt="Bosch" className="bosch-logo-image" />
          <div className="header-divider topbar-divider" />
          <div className="platform-brand">
            <h1>Helix Agentic Platform</h1>
            <p>From operational realities to aligned SOPs, with AI</p>
          </div>
        </div>
        <EmployeeProfileMenu role="Admin" placement="header" profileClassName="employee-profile" />
      </header>

      <div className="employee-body">
        <aside className="employee-sidebar admin-sidebar">
          <nav aria-label="Admin navigation">
            <button
              className={`sidebar-item ${activeView === 'overview' ? 'active' : ''}`}
              type="button"
              onClick={() => openView('overview')}
            >
              <Grid2X2 size={17} /> Overview
            </button>
            <button
              className={`sidebar-item ${activeView === 'repository' ? 'active' : ''}`}
              type="button"
              onClick={() => openView('repository')}
            >
              <FolderOpen size={17} /> Source Management
            </button>
            <button
              className={`sidebar-item ${activeView === 'analytics' ? 'active' : ''}`}
              type="button"
              onClick={() => openView('analytics')}
            >
              <Activity size={17} /> Analytics
            </button>
          </nav>
          <div className="admin-sidebar-bottom">
            <button
              className={`sidebar-item ${activeView === 'settings' ? 'active' : ''}`}
              type="button"
              onClick={() => openView('settings')}
            >
              <Settings size={17} /> Settings
            </button>
            <EmployeeProfileMenu
              role="Admin"
              placement="sidebar"
              wrapperClassName="sidebar-bottom"
              profileClassName="sidebar-user"
            />
          </div>
        </aside>

        <main className="employee-main admin-main">
          {activeView === 'overview' && renderOverview()}
          {activeView === 'repository' && renderRepository()}
          {activeView === 'analytics' && renderAnalytics()}
          {activeView === 'settings' && renderSettings()}
        </main>
      </div>

      {selectedSource && (
        <div
          className="admin-modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeSource();
          }}
        >
          <section
            className="admin-source-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="admin-source-modal-title"
          >
            <div className="admin-source-modal-header">
              <div className="admin-source-modal-heading">
                <span className={`admin-source-icon ${selectedSource.type}`}>
                  {(() => {
                    const Icon = selectedSource.icon;
                    return <Icon size={21} />;
                  })()}
                </span>
                <div>
                  <span className="admin-source-category">SOURCE CONFIGURATION</span>
                  <h2 id="admin-source-modal-title">{selectedSource.title}</h2>
                </div>
              </div>
              <button
                type="button"
                className="admin-modal-close"
                aria-label="Close source configuration"
                onClick={closeSource}
              >
                <X size={19} />
              </button>
            </div>

            <p className="admin-source-modal-description">{selectedSource.description}</p>

            <form onSubmit={handleSourceSubmit}>
              <label className="admin-source-field-label" htmlFor="admin-source-title">
                Source Title / Label (Optional)
              </label>
              <input
                id="admin-source-title"
                className="admin-source-input"
                type="text"
                value={sourceTitle}
                onChange={(event) => setSourceTitle(event.target.value)}
                placeholder={`e.g., ${selectedSource.title} process reference`}
                maxLength={120}
              />

              {selectedSource.id === 'docupedia' && (
                <>
                  <div className="admin-source-mode-switch" role="tablist" aria-label="Docupedia input method">
                    <button type="button" role="tab" aria-selected={docupediaMode === 'upload'} className={docupediaMode === 'upload' ? 'active' : ''} onClick={() => { setDocupediaMode('upload'); setSelectedFiles([]); setSourceValue(''); setNotice(''); }}>
                      Upload PDF / Document
                    </button>
                    <button type="button" role="tab" aria-selected={docupediaMode === 'url'} className={docupediaMode === 'url' ? 'active' : ''} onClick={() => { setDocupediaMode('url'); setSelectedFiles([]); setNotice(''); }}>
                      Docupedia URL
                    </button>
                  </div>
                  {docupediaMode === 'upload' ? (
                    <>
                      <label className="admin-source-field-label" htmlFor="admin-source-files">Upload Document / PDF</label>
                      <label className={`admin-file-dropzone ${isDraggingFiles ? 'is-dragging' : ''}`} htmlFor="admin-source-files" onDragOver={(event) => { event.preventDefault(); setIsDraggingFiles(true); }} onDragLeave={() => setIsDraggingFiles(false)} onDrop={(event) => { event.preventDefault(); setIsDraggingFiles(false); handleFiles(event.dataTransfer.files); }}>
                        <UploadCloud size={27} />
                        <strong>Click to browse or drag and drop</strong>
                        <span>Accepts PDF, DOCX, DOC, TXT files up to 50 MB</span>
                        <input id="admin-source-files" type="file" accept=".pdf,.docx,.doc,.txt" multiple={false} onChange={(event) => handleFiles(event.target.files)} />
                      </label>
                    </>
                  ) : (
                    <>
                      <label className="admin-source-field-label" htmlFor="admin-source-input">Docupedia URL</label>
                      <input id="admin-source-input" className="admin-source-input" type="url" value={sourceValue} onChange={(event) => setSourceValue(event.target.value)} placeholder="https://docupedia.bosch.com/wiki/..." required />
                    </>
                  )}
                </>
              )}

              {selectedSource.id === 'outlook' && (
                <>
                  <div className="admin-source-mode-switch" role="tablist" aria-label="Outlook input method">
                    <button type="button" role="tab" aria-selected={outlookMode === 'upload'} className={outlookMode === 'upload' ? 'active' : ''} onClick={() => { setOutlookMode('upload'); setSelectedFiles([]); setSourceValue(''); setNotice(''); }}>
                      Upload Exported Email
                    </button>
                    <button type="button" role="tab" aria-selected={outlookMode === 'info'} className={outlookMode === 'info' ? 'active' : ''} onClick={() => { setOutlookMode('info'); setSelectedFiles([]); setNotice(''); }}>
                      Reference Details
                    </button>
                  </div>
                  {outlookMode === 'upload' ? (
                    <>
                      <label className="admin-source-field-label" htmlFor="admin-source-files">Upload Exported Email</label>
                      <label className={`admin-file-dropzone ${isDraggingFiles ? 'is-dragging' : ''}`} htmlFor="admin-source-files" onDragOver={(event) => { event.preventDefault(); setIsDraggingFiles(true); }} onDragLeave={() => setIsDraggingFiles(false)} onDrop={(event) => { event.preventDefault(); setIsDraggingFiles(false); handleFiles(event.dataTransfer.files); }}>
                        <UploadCloud size={27} />
                        <strong>Click to browse or drag and drop</strong>
                        <span>Accepts MSG, EML, PDF, TXT files up to 50 MB</span>
                        <input id="admin-source-files" type="file" accept=".msg,.eml,.pdf,.txt" multiple onChange={(event) => handleFiles(event.target.files)} />
                      </label>
                    </>
                  ) : (
                    <>
                      <label className="admin-source-field-label" htmlFor="admin-source-input">Correspondence Details</label>
                      <textarea id="admin-source-input" className="admin-source-input admin-source-textarea" value={sourceValue} onChange={(event) => setSourceValue(event.target.value)} placeholder="Summary of process emails and transformation notices..." rows={4} required />
                      <p className="admin-source-help">You can provide email context or reference details here. This does not connect to your Outlook mailbox.</p>
                    </>
                  )}
                </>
              )}

              {selectedSource.inputType === 'url' && selectedSource.id !== 'docupedia' && (
                <>
                  <label className="admin-source-field-label" htmlFor="admin-source-input">{selectedSource.inputLabel}</label>
                  <input id="admin-source-input" className="admin-source-input" type="url" value={sourceValue} onChange={(event) => setSourceValue(event.target.value)} placeholder={selectedSource.placeholder || 'https://...'} required />
                </>
              )}

              {selectedSource.inputType === 'file' && (
                <>
                  <label className="admin-source-field-label" htmlFor="admin-source-files">{selectedSource.inputLabel}</label>
                  <label className={`admin-file-dropzone ${isDraggingFiles ? 'is-dragging' : ''}`} htmlFor="admin-source-files" onDragOver={(event) => { event.preventDefault(); setIsDraggingFiles(true); }} onDragLeave={() => setIsDraggingFiles(false)} onDrop={(event) => { event.preventDefault(); setIsDraggingFiles(false); handleFiles(event.dataTransfer.files); }}>
                    <UploadCloud size={27} />
                    <strong>Click to browse or drag and drop</strong>
                    <span>{selectedSource.id === 'audio' ? 'Audio files' : selectedSource.id === 'video' ? 'Video files' : 'PDF, DOCX, DOC, XLSX, XLS, PPTX, PPT, TXT files'} · up to 50 MB per file</span>
                    <input id="admin-source-files" type="file" accept={selectedSource.accept} multiple={selectedSource.multiple} onChange={(event) => handleFiles(event.target.files)} required />
                  </label>
                </>
              )}

              {selectedSource.id === 'paste-text' && (
                <>
                  <label className="admin-source-field-label" htmlFor="admin-source-input">Paste Process Information</label>
                  <textarea id="admin-source-input" className="admin-source-input admin-source-textarea" value={sourceValue} onChange={(event) => setSourceValue(event.target.value)} placeholder="Paste workflow steps, operational handoffs, business rules..." rows={7} required />
                </>
              )}

              {selectedFiles.length > 0 && (
                <div className="admin-selected-files" aria-live="polite">
                  {selectedFiles.map((file) => (
                    <div key={`${file.name}-${file.lastModified || file.size}`}>
                      <FileText size={15} />
                      <span>{file.name}</span>
                      <span className="admin-file-size">{(file.size / 1024 / 1024).toFixed(2)} MB</span>
                      <button type="button" className="admin-remove-selected-file" aria-label={`Remove ${file.name}`} onClick={() => setSelectedFiles((current) => current.filter((item) => item !== file))}><X size={14} /></button>
                    </div>
                  ))}
                </div>
              )}

              <label className="admin-source-field-label" htmlFor="admin-source-notes">Transformation Context / Notes (Optional)</label>
              <textarea
                id="admin-source-notes"
                className="admin-source-input admin-source-notes"
                value={sourceNotes}
                onChange={(event) => setSourceNotes(event.target.value)}
                placeholder="Optional context, process area, transformation background, or notes..."
                rows={3}
              />

              <p className="admin-source-modal-note">
                Preview only. Submitting creates a session-only record. This form does not upload files,
                connect to external repositories, or run AI processing.
              </p>

              {notice && (
                <div className={`admin-source-notice ${noticeType}`} role="status">
                  {noticeType === 'error' ? <X size={17} /> : <CheckCircle2 size={17} />}
                  <span>{notice}</span>
                </div>
              )}

              <div className="admin-source-modal-actions">
                <button type="button" className="admin-secondary-button" onClick={closeSource}>Cancel</button>
                <button type="submit" className="admin-primary-button">Add Preview Record</button>
              </div>
            </form>
          </section>
        </div>
      )}
    </div>
  );
}

export default AdminDashboard;
