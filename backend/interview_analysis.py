import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_ANALYSIS_PAYLOAD_BYTES = 1_000_000
Assessment = Literal[
    "understood",
    "partially_understood",
    "not_understood",
    "insufficient_evidence",
]


class AnalysisModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class KnowledgeSourceSnapshot(AnalysisModel):
    knowledge_source_id_snapshot: UUID
    source_title_snapshot: str = Field(min_length=1, max_length=500)


class InterviewAnalysisEvidence(AnalysisModel):
    question_id: UUID
    answer_id: UUID
    answer_excerpt: str = Field(min_length=1, max_length=2_000)
    supporting_knowledge_sources: list[KnowledgeSourceSnapshot] = Field(max_length=20)


class AssessmentDimension(AnalysisModel):
    assessment: Assessment
    rationale: str = Field(min_length=1, max_length=2_000)
    evidence: list[InterviewAnalysisEvidence] = Field(max_length=50)

    @model_validator(mode="after")
    def require_evidence_for_assessment(self):
        if self.assessment != "insufficient_evidence" and not self.evidence:
            raise ValueError("An assessed dimension requires answer evidence.")
        if self.assessment != "insufficient_evidence" and not any(
            evidence.supporting_knowledge_sources for evidence in self.evidence
        ):
            raise ValueError(
                "A substantive dimension assessment requires supporting knowledge sources."
            )
        return self


class InterviewAnalysisDimensions(AnalysisModel):
    process_understanding: AssessmentDimension
    role_clarity: AssessmentDimension
    workflow_knowledge: AssessmentDimension
    exception_handling: AssessmentDimension
    knowledge_gaps: AssessmentDimension


class AnalysisFinding(AnalysisModel):
    statement: str = Field(min_length=1, max_length=2_000)
    evidence: list[InterviewAnalysisEvidence] = Field(max_length=50)
    related_question_ids: list[UUID] = Field(max_length=100)

    @model_validator(mode="after")
    def require_a_session_reference(self):
        if not self.evidence and not self.related_question_ids:
            raise ValueError("A finding must reference evidence or a related question.")
        return self


class RecommendedFollowUpQuestion(AnalysisModel):
    question_text: str = Field(min_length=1, max_length=2_000)
    rationale: str = Field(min_length=1, max_length=2_000)
    related_question_ids: list[UUID] = Field(min_length=1, max_length=100)
    evidence: list[InterviewAnalysisEvidence] = Field(max_length=50)


class ProviderModelMetadata(AnalysisModel):
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=64)


class InterviewAnalysis(AnalysisModel):
    schema_version: Literal["1.0"]
    session_id: UUID
    summary: str = Field(min_length=1, max_length=4_000)
    dimensions: InterviewAnalysisDimensions
    strengths: list[AnalysisFinding] = Field(max_length=50)
    improvement_areas: list[AnalysisFinding] = Field(max_length=50)
    identified_gaps: list[AnalysisFinding] = Field(max_length=50)
    recommended_follow_up_questions: list[RecommendedFollowUpQuestion] = Field(
        max_length=30
    )
    provider_metadata: ProviderModelMetadata
    analysis_timestamp: datetime

    @field_validator("analysis_timestamp")
    @classmethod
    def require_timezone_aware_timestamp(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Analysis timestamp must include a timezone.")
        return value


def parse_interview_analysis(value) -> InterviewAnalysis:
    analysis = InterviewAnalysis.model_validate(value)
    serialized = json.dumps(
        analysis.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(serialized) > MAX_ANALYSIS_PAYLOAD_BYTES:
        raise ValueError("Interview analysis exceeds the 1 MB payload limit.")
    return analysis


def validate_analysis_evidence(
    analysis: InterviewAnalysis | dict, session_details: dict
) -> InterviewAnalysis:
    parsed = parse_interview_analysis(analysis)
    if str(parsed.session_id) != str(session_details.get("id")):
        raise ValueError("Interview analysis session ID does not match the session.")

    questions = {
        str(question["id"]): question
        for question in session_details.get("questions", [])
    }

    def validate_evidence(evidence_items):
        for evidence in evidence_items:
            question_id = str(evidence.question_id)
            question = questions.get(question_id)
            if question is None:
                raise ValueError("Evidence question does not belong to this session.")

            answers = {
                str(answer["id"]): answer
                for answer in question.get("answers", [])
            }
            answer = answers.get(str(evidence.answer_id))
            if answer is None:
                raise ValueError("Evidence answer does not belong to the cited question.")
            if evidence.answer_excerpt not in answer["answer_text"]:
                raise ValueError("Evidence excerpt is not an exact part of the cited answer.")

            source_snapshots = {
                (
                    str(source["knowledge_source_id_snapshot"]),
                    source["source_title_snapshot"],
                )
                for source in question.get("knowledge_sources", [])
            }
            for source in evidence.supporting_knowledge_sources:
                reference = (
                    str(source.knowledge_source_id_snapshot),
                    source.source_title_snapshot,
                )
                if reference not in source_snapshots:
                    raise ValueError(
                        "Knowledge source reference does not belong to the cited question."
                    )

    def validate_question_ids(question_ids):
        if any(str(question_id) not in questions for question_id in question_ids):
            raise ValueError("Related question does not belong to this session.")

    for dimension_name in InterviewAnalysisDimensions.model_fields:
        dimension = getattr(parsed.dimensions, dimension_name)
        validate_evidence(dimension.evidence)

    for findings in (
        parsed.strengths,
        parsed.improvement_areas,
        parsed.identified_gaps,
    ):
        for finding in findings:
            validate_evidence(finding.evidence)
            validate_question_ids(finding.related_question_ids)

    for follow_up in parsed.recommended_follow_up_questions:
        validate_evidence(follow_up.evidence)
        validate_question_ids(follow_up.related_question_ids)

    return parsed