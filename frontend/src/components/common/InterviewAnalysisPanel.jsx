import {
  AlertCircle,
  CheckCircle2,
  FileText,
  LoaderCircle,
  RefreshCw,
  Sparkles,
} from 'lucide-react';

const DIMENSIONS = [
  ['process_understanding', 'Process understanding'],
  ['role_clarity', 'Role clarity'],
  ['workflow_knowledge', 'Workflow knowledge'],
  ['exception_handling', 'Exception handling'],
  ['knowledge_gaps', 'Knowledge gaps'],
];

const ASSESSMENT_LABELS = {
  understood: 'Understood',
  partially_understood: 'Partially understood',
  not_understood: 'Not understood',
  insufficient_evidence: 'Insufficient evidence',
};

function EvidenceItem({ evidence, questions }) {
  const questionIndex = questions.findIndex(
    (question) => question.id === evidence.question_id,
  );
  const question = questions[questionIndex];
  const answer = question?.answers?.find((item) => item.id === evidence.answer_id);
  const answerIndex = question?.answers?.findIndex(
    (item) => item.id === evidence.answer_id,
  ) ?? -1;

  return (
    <article className="analysis-evidence-item">
      <div className="analysis-evidence-links">
        {question && (
          <a href={`#interview-question-${question.id}`}>
            Question {(question.question_order ?? questionIndex) + 1}
          </a>
        )}
        {answer && (
          <a href={`#interview-answer-${answer.id}`}>
            Answer {(answer.answer_order ?? answerIndex) + 1}
          </a>
        )}
        {!question && <span>Interview reference unavailable</span>}
      </div>

      {evidence.answer_excerpt ? (
        <blockquote>{evidence.answer_excerpt}</blockquote>
      ) : (
        <p className="analysis-empty-evidence">No answer excerpt was provided.</p>
      )}

      {evidence.supporting_knowledge_sources?.length > 0 ? (
        <ul className="analysis-source-list">
          {evidence.supporting_knowledge_sources.map((source) => {
            const matchingReference = questions
              .flatMap((item) => item.knowledge_sources || [])
              .find((reference) => (
                reference.knowledge_source_id_snapshot
                  === source.knowledge_source_id_snapshot
              ));
            const isUnavailable = matchingReference
              && matchingReference.knowledge_source_id === null;

            return (
              <li key={`${source.knowledge_source_id_snapshot}-${source.source_title_snapshot}`}>
                <FileText size={14} aria-hidden="true" />
                <span>
                  {source.source_title_snapshot || 'Untitled source'}
                  {isUnavailable && (
                    <span className="analysis-source-unavailable">Source no longer available</span>
                  )}
                  <code>{source.knowledge_source_id_snapshot}</code>
                </span>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="analysis-no-sources">No supporting knowledge source cited.</p>
      )}
    </article>
  );
}

function EvidenceList({ evidence, questions }) {
  if (!evidence?.length) {
    return <p className="analysis-empty-evidence">No answer evidence is available.</p>;
  }

  return (
    <div className="analysis-evidence-list">
      {evidence.map((item, index) => (
        <EvidenceItem
          key={`${item.question_id}-${item.answer_id}-${index}`}
          evidence={item}
          questions={questions}
        />
      ))}
    </div>
  );
}

function FindingSection({ title, items, emptyMessage, questions }) {
  return (
    <section className="analysis-findings-section">
      <h3>{title}</h3>
      {items.length ? (
        <ul className="analysis-finding-list">
          {items.map((item, index) => (
            <li key={`${item.statement}-${index}`}>
              <p>{item.statement}</p>
              <EvidenceList evidence={item.evidence} questions={questions} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="analysis-section-empty">{emptyMessage}</p>
      )}
    </section>
  );
}

function InterviewAnalysisPanel({
  history,
  selectedAnalysisId,
  onSelectAnalysis,
  status,
  error,
  onGenerate,
  isGenerating,
  questions,
}) {
  const selectedRecord = history.find((record) => record.id === selectedAnalysisId)
    || history[history.length - 1]
    || null;
  const analysis = selectedRecord?.analysis;

  return (
    <section className="interview-analysis-panel" aria-labelledby="analysis-heading">
      <header className="analysis-panel-header">
        <div>
          <span className="analysis-eyebrow">COMPLETED SESSION</span>
          <h2 id="analysis-heading">Interview analysis</h2>
          <p>Evidence-linked review of the recorded interview.</p>
        </div>
        <button
          className="analysis-generate-button"
          type="button"
          onClick={onGenerate}
          disabled={isGenerating || status === 'loading'}
        >
          {isGenerating ? <LoaderCircle size={16} className="analysis-spinner" />
            : history.length ? <RefreshCw size={15} /> : <Sparkles size={16} />}
          {isGenerating
            ? 'Generating analysis...'
            : history.length ? 'Regenerate analysis' : 'Generate AI analysis'}
        </button>
      </header>

      {history.length > 1 && (
        <div className="analysis-history-control">
          <label htmlFor="analysis-history">Analysis history</label>
          <select
            id="analysis-history"
            value={selectedRecord?.id || ''}
            onChange={(event) => onSelectAnalysis(event.target.value)}
          >
            {history.map((record, index) => (
              <option key={record.id} value={record.id}>
                Analysis {index + 1} · {new Date(record.created_at).toLocaleString()}
              </option>
            ))}
          </select>
        </div>
      )}

      {error && (
        <div className={`analysis-alert is-${error.kind}`} role="alert">
          <AlertCircle size={17} />
          <div>
            <strong>{error.title}</strong>
            <p>{error.message}</p>
          </div>
        </div>
      )}

      {status === 'loading' && (
        <div className="analysis-state" role="status">
          <LoaderCircle size={19} className="analysis-spinner" />
          <span>Loading saved analysis...</span>
        </div>
      )}

      {status === 'generating' && (
        <div className="analysis-state is-generating" role="status">
          <LoaderCircle size={19} className="analysis-spinner" />
          <span>Reviewing the completed interview evidence...</span>
        </div>
      )}

      {!analysis && status === 'empty' && (
        <div className="analysis-empty-state">
          <div className="analysis-empty-icon"><Sparkles size={19} /></div>
          <div>
            <h3>No analysis yet</h3>
            <p>Generate an analysis when you are ready. Existing answers will remain unchanged.</p>
          </div>
        </div>
      )}

      {!analysis && status === 'error' && !error && (
        <div className="analysis-empty-state" role="status">
          <div className="analysis-empty-icon"><AlertCircle size={19} /></div>
          <div>
            <h3>Analysis could not be loaded</h3>
            <p>Try reloading the saved analysis.</p>
          </div>
        </div>
      )}

      {analysis && (
        <div className="analysis-results">
          {status === 'ready' && (
            <div className="analysis-saved-status" role="status">
              <CheckCircle2 size={15} /> Saved analysis
            </div>
          )}

          <section className="analysis-summary">
            <span>OVERALL ASSESSMENT</span>
            <p>{analysis.summary}</p>
          </section>

          <section className="analysis-dimensions" aria-label="Assessment dimensions">
            {DIMENSIONS.map(([key, label]) => {
              const dimension = analysis.dimensions[key];
              const statusLabel = ASSESSMENT_LABELS[dimension.assessment]
                || 'Assessment unavailable';
              return (
                <article className="analysis-dimension" key={key}>
                  <div className="analysis-dimension-heading">
                    <h3>{label}</h3>
                    <span className={`analysis-status status-${dimension.assessment}`}>
                      {statusLabel}
                    </span>
                  </div>
                  <p className="analysis-rationale">{dimension.rationale}</p>
                  <EvidenceList evidence={dimension.evidence} questions={questions} />
                </article>
              );
            })}
          </section>

          <div className="analysis-findings-grid">
            <FindingSection
              title="Strengths"
              items={analysis.strengths}
              emptyMessage="No strengths were identified."
              questions={questions}
            />
            <FindingSection
              title="Improvement areas"
              items={analysis.improvement_areas}
              emptyMessage="No improvement areas were identified."
              questions={questions}
            />
            <FindingSection
              title="Identified knowledge gaps"
              items={analysis.identified_gaps}
              emptyMessage="No knowledge gaps were identified."
              questions={questions}
            />
          </div>

          <section className="analysis-findings-section analysis-follow-ups">
            <h3>Recommended follow-up questions</h3>
            {analysis.recommended_follow_up_questions.length ? (
              <ol className="analysis-follow-up-list">
                {analysis.recommended_follow_up_questions.map((item, index) => (
                  <li key={`${item.question_text}-${index}`}>
                    <strong>{item.question_text}</strong>
                    <p>{item.rationale}</p>
                    <div className="analysis-evidence-links">
                      {item.related_question_ids.map((questionId) => {
                        const questionIndex = questions.findIndex(
                          (question) => question.id === questionId,
                        );
                        return questionIndex >= 0 ? (
                          <a
                            href={`#interview-question-${questionId}`}
                            key={questionId}
                          >
                            Related question {(questions[questionIndex].question_order
                              ?? questionIndex) + 1}
                          </a>
                        ) : null;
                      })}
                    </div>
                    <EvidenceList evidence={item.evidence} questions={questions} />
                  </li>
                ))}
              </ol>
            ) : (
              <p className="analysis-section-empty">No follow-up questions were recommended.</p>
            )}
          </section>

          <footer className="analysis-metadata">
            <span>{analysis.provider_metadata.provider} · {analysis.provider_metadata.model}</span>
            <span>Prompt {analysis.provider_metadata.prompt_version}</span>
            <time dateTime={analysis.analysis_timestamp}>
              {new Date(analysis.analysis_timestamp).toLocaleString()}
            </time>
          </footer>
        </div>
      )}
    </section>
  );
}

export default InterviewAnalysisPanel;