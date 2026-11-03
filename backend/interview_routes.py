"""Employee-owned AI Interview Studio session APIs."""

import logging
from concurrent.futures import Future
from threading import Lock
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from interview_analysis import InterviewAnalysis
from interview_analysis_service import (
    InterviewAnalysisContextError,
    InterviewAnalysisProviderConfigurationError,
    InterviewAnalysisProviderError,
    InterviewAnalysisSessionNotCompletedError,
    InterviewAnalysisSessionNotFoundError,
    InterviewAnalysisValidationError,
    InterviewContextLimitError,
    generate_interview_analysis,
)
from interview_question_service import (
    GeneratedNextInterviewQuestion,
    InterviewQuestionGenerationError,
    InterviewQuestionProviderConfigurationError,
    InterviewQuestionProviderError,
    InterviewQuestionValidationError,
    generate_first_interview_question,
    generate_next_interview_question,
)
import knowledge_store
from knowledge_routes import require_employee


router = APIRouter(tags=["Interviews"])
SessionStatus = Literal["created", "in_progress", "completed", "failed"]
logger = logging.getLogger(__name__)
_analysis_inflight_guard = Lock()
_analysis_inflight: dict[str, Future] = {}


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_id: str = Field(min_length=1, max_length=64)


class QuestionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_text: str = Field(min_length=1, max_length=20_000)
    knowledge_source_ids: list[UUID] = Field(default_factory=list, max_length=50)


class AnswerCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer_text: str = Field(min_length=1, max_length=200_000)


class KnowledgeSourceReference(BaseModel):
    knowledge_source_id: UUID | None
    knowledge_source_id_snapshot: UUID
    source_title_snapshot: str
    created_at: str


class InterviewAnswer(BaseModel):
    id: UUID
    question_id: UUID
    answer_order: int
    answer_text: str
    submitted_at: str


class InterviewQuestion(BaseModel):
    id: UUID
    session_id: UUID
    question_order: int
    question_text: str
    created_at: str
    knowledge_sources: list[KnowledgeSourceReference]
    answers: list[InterviewAnswer] = Field(default_factory=list)


class InterviewSession(BaseModel):
    id: UUID
    employee_id: str
    category_id: str | None = None
    knowledge_source_ids: list[UUID] = Field(default_factory=list)
    status: SessionStatus
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None


class InterviewSessionDetails(InterviewSession):
    questions: list[InterviewQuestion]


class InterviewSessionResponse(BaseModel):
    session: InterviewSessionDetails


class InterviewSessionListResponse(BaseModel):
    sessions: list[InterviewSession]


class InterviewQuestionResponse(BaseModel):
    question: InterviewQuestion


class InterviewAnswerResponse(BaseModel):
    answer: InterviewAnswer


class NextInterviewQuestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    should_continue: bool
    question: InterviewQuestion | None


class InterviewCategory(BaseModel):
    id: str
    name: str
    source_count: int = Field(ge=1)


class InterviewCategoriesResponse(BaseModel):
    categories: list[InterviewCategory]


class InterviewAnalysisRecordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    session_id: UUID
    analysis: InterviewAnalysis
    created_at: str


class InterviewAnalysisHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID
    latest: InterviewAnalysisRecordResponse | None
    history: list[InterviewAnalysisRecordResponse]


def _owned_session(session_id: UUID, employee_id: str) -> dict:
    session = knowledge_store.get_interview_session(str(session_id))
    if session is None or session["employee_id"] != employee_id:
        raise HTTPException(status_code=404, detail="Interview session not found.")
    return session


def _session_details(session_id: UUID) -> dict:
    session = knowledge_store.get_interview_session_details(str(session_id))
    if session is None:
        raise HTTPException(status_code=404, detail="Interview session not found.")
    return session


def _conflict(error: knowledge_store.InterviewSessionConflictError):
    raise HTTPException(status_code=409, detail=str(error)) from error


def _generate_analysis_once(session_id: UUID) -> dict:
    key = str(session_id)
    with _analysis_inflight_guard:
        future = _analysis_inflight.get(key)
        is_owner = future is None
        if is_owner:
            future = Future()
            _analysis_inflight[key] = future

    if not is_owner:
        return future.result()

    try:
        result = generate_interview_analysis(key)
    except Exception as error:
        future.set_exception(error)
        raise
    else:
        future.set_result(result)
        return result
    finally:
        with _analysis_inflight_guard:
            if _analysis_inflight.get(key) is future:
                del _analysis_inflight[key]


def _analysis_generation_error(error: Exception):
    if isinstance(error, InterviewAnalysisProviderConfigurationError):
        raise HTTPException(
            status_code=503,
            detail="Interview analysis provider is not configured.",
        ) from None
    if isinstance(error, InterviewAnalysisProviderError):
        raise HTTPException(
            status_code=502,
            detail="Interview analysis provider failed. No analysis was saved.",
        ) from None
    if isinstance(error, InterviewAnalysisValidationError):
        raise HTTPException(
            status_code=422,
            detail="Generated analysis failed validation. No analysis was saved.",
        ) from None
    if isinstance(error, InterviewContextLimitError):
        raise HTTPException(
            status_code=413,
            detail="Interview context exceeds configured analysis limits.",
        ) from None
    if isinstance(error, InterviewAnalysisSessionNotCompletedError):
        raise HTTPException(
            status_code=409,
            detail="Interview analysis requires a completed session.",
        ) from None
    if isinstance(error, InterviewAnalysisSessionNotFoundError):
        raise HTTPException(status_code=404, detail="Interview session not found.") from None
    if isinstance(error, InterviewAnalysisContextError):
        raise HTTPException(
            status_code=422,
            detail="Interview context could not be prepared for analysis.",
        ) from None
    logger.error("Interview analysis failed unexpectedly (%s).", type(error).__name__)
    raise HTTPException(
        status_code=500,
        detail="Interview analysis could not be completed.",
    ) from None


def _first_question_generation_error(error: Exception):
    if isinstance(error, InterviewQuestionProviderConfigurationError):
        raise HTTPException(
            status_code=503,
            detail="Interview question generation is not configured.",
        ) from None
    if isinstance(error, InterviewQuestionProviderError):
        provider_status = error.status_code
        logger.warning(
            "Gemini first-question provider failure status=%s",
            provider_status,
        )
        provider_failures = {
            400: (
                502,
                "Gemini rejected the question request. Check the configured model and request settings.",
            ),
            401: (
                503,
                "Gemini rejected the configured API key. Verify GEMINI_API_KEY and restart the backend.",
            ),
            403: (
                503,
                "Gemini denied project access. Verify API enablement, key restrictions, and project permissions.",
            ),
            404: (
                503,
                "The configured Gemini question model is unavailable to this API project.",
            ),
            429: (
                429,
                "Gemini quota or rate limit reached. Check project quota and retry later.",
            ),
        }
        if provider_status in provider_failures:
            status, detail = provider_failures[provider_status]
        elif provider_status is None:
            status = 502
            detail = (
                "Gemini could not complete the request. Check backend connectivity, "
                "HTTPS proxy configuration, and provider response availability."
            )
        else:
            status = 502
            detail = (
                f"Gemini question provider returned HTTP {provider_status}. "
                "No question was saved."
            )
        raise HTTPException(
            status_code=status,
            detail=detail,
        ) from None
    if isinstance(error, InterviewQuestionValidationError):
        raise HTTPException(
            status_code=422,
            detail="Generated question failed validation. No question was saved.",
        ) from None
    if isinstance(error, knowledge_store.InterviewSessionNotFoundError):
        raise HTTPException(status_code=404, detail="Interview session not found.") from None
    if isinstance(error, knowledge_store.InterviewSessionConflictError):
        raise HTTPException(
            status_code=409,
            detail="The initial question cannot be generated for this session.",
        ) from None
    if isinstance(error, InterviewQuestionGenerationError):
        raise HTTPException(
            status_code=422,
            detail="Interview question could not be generated.",
        ) from None
    logger.error("Interview first-question generation failed (%s).", type(error).__name__)
    raise HTTPException(
        status_code=500,
        detail="Interview question could not be generated.",
    ) from None


@router.get(
    "/api/interviews/categories",
    response_model=InterviewCategoriesResponse,
)
def get_interview_categories(user=Depends(require_employee)):
    del user
    return {"categories": knowledge_store.list_interview_categories()}


@router.post("/api/interviews/sessions", response_model=InterviewSessionResponse, status_code=201)
def create_session(
    request: SessionCreateRequest,
    user=Depends(require_employee),
):
    try:
        session = knowledge_store.create_interview_session(
            user["sub"], request.category_id
        )
    except knowledge_store.InterviewCategoryUnavailableError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"session": knowledge_store.get_interview_session_details(session["id"])}


@router.get("/api/interviews/sessions", response_model=InterviewSessionListResponse)
def list_sessions(user=Depends(require_employee)):
    return {"sessions": knowledge_store.list_interview_sessions(user["sub"])}


@router.get("/api/interviews/sessions/{session_id}", response_model=InterviewSessionResponse)
def get_session(session_id: UUID, user=Depends(require_employee)):
    _owned_session(session_id, user["sub"])
    return {"session": _session_details(session_id)}


@router.post(
    "/api/interviews/sessions/{session_id}/questions",
    response_model=InterviewQuestionResponse,
    status_code=201,
)
def create_question(
    session_id: UUID,
    request: QuestionCreateRequest,
    user=Depends(require_employee),
):
    _owned_session(session_id, user["sub"])
    try:
        question = knowledge_store.add_interview_question(
            str(session_id),
            request.question_text,
            [str(source_id) for source_id in request.knowledge_source_ids],
        )
    except knowledge_store.InterviewSessionConflictError as error:
        _conflict(error)
    except knowledge_store.InterviewSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    question["answers"] = []
    return {"question": question}


@router.post(
    "/api/interviews/sessions/{session_id}/first-question",
    response_model=InterviewQuestionResponse,
)
def generate_first_question(session_id: UUID, user=Depends(require_employee)):
    session = _owned_session(session_id, user["sub"])
    if session["status"] in {"completed", "failed"}:
        raise HTTPException(
            status_code=409,
            detail="A first question cannot be generated for a completed or failed session.",
        )
    category_id = session.get("category_id")
    if not category_id:
        raise HTTPException(
            status_code=409,
            detail="The interview session has no selected knowledge category.",
        )

    try:
        details = _session_details(session_id)
        if details["questions"]:
            return {"question": details["questions"][0]}

        scoped_context = knowledge_store.get_interview_category_context(
            str(session_id), category_id
        )
        if not scoped_context["sources"]:
            raise HTTPException(
                status_code=409,
                detail="No eligible knowledge remains in the selected category.",
            )
        generated = generate_first_interview_question(scoped_context)
        question, _created = knowledge_store.add_initial_interview_question(
            str(session_id),
            generated.question_text,
            [str(source_id) for source_id in generated.source_ids],
            source_content_snapshots={
                source["id"]: source["content"]
                for source in scoped_context["sources"]
            },
        )
        return {"question": question}
    except HTTPException:
        raise
    except Exception as error:
        _first_question_generation_error(error)


def _next_question_generation_error(error: Exception):
    if isinstance(error, InterviewQuestionProviderConfigurationError):
        raise HTTPException(
            status_code=503,
            detail="Interview question generation is not configured.",
        ) from None
    if isinstance(error, InterviewQuestionProviderError):
        raise HTTPException(
            status_code=502,
            detail="Interview follow-up provider failed. No question was saved.",
        ) from None
    if isinstance(error, InterviewQuestionValidationError):
        raise HTTPException(
            status_code=422,
            detail="Generated follow-up failed validation. No question was saved.",
        ) from None
    if isinstance(error, knowledge_store.InterviewQuestionNotFoundError):
        raise HTTPException(status_code=404, detail="Interview question not found.") from None
    if isinstance(error, knowledge_store.InterviewSessionNotFoundError):
        raise HTTPException(status_code=404, detail="Interview session not found.") from None
    if isinstance(error, knowledge_store.InterviewSessionConflictError):
        raise HTTPException(
            status_code=409,
            detail="A follow-up question cannot be generated for the current conversation state.",
        ) from None
    if isinstance(error, InterviewQuestionGenerationError):
        raise HTTPException(
            status_code=422,
            detail="Interview follow-up could not be generated.",
        ) from None
    logger.error("Interview follow-up generation failed (%s).", type(error).__name__)
    raise HTTPException(
        status_code=500,
        detail="Interview follow-up could not be generated.",
    ) from None


@router.post(
    "/api/interviews/sessions/{session_id}/next-question",
    response_model=NextInterviewQuestionResponse,
)
def generate_next_question(session_id: UUID, user=Depends(require_employee)):
    session = _owned_session(session_id, user["sub"])
    if session["status"] in {"completed", "failed"}:
        raise HTTPException(
            status_code=409,
            detail="A follow-up question cannot be generated for a completed or failed session.",
        )
    category_id = session.get("category_id")
    if not category_id:
        raise HTTPException(
            status_code=409,
            detail="The interview session has no selected knowledge category.",
        )

    details = _session_details(session_id)
    questions = details["questions"]
    if not questions:
        raise HTTPException(
            status_code=409,
            detail="Generate the first interview question before requesting a follow-up.",
        )

    latest_question = questions[-1]
    if not latest_question["answers"]:
        if len(questions) > 1 and questions[-2]["answers"]:
            return {"should_continue": True, "question": latest_question}
        raise HTTPException(
            status_code=409,
            detail="Submit an answer to the latest interview question first.",
        )

    try:
        scoped_context = knowledge_store.get_interview_category_context(
            str(session_id), category_id
        )
        if not scoped_context["sources"]:
            raise HTTPException(
                status_code=409,
                detail="No eligible knowledge remains in the selected category.",
            )
        decision: GeneratedNextInterviewQuestion = generate_next_interview_question(
            scoped_context, questions
        )
        if not decision.should_continue:
            return {"should_continue": False, "question": None}
        question, _created = knowledge_store.add_adaptive_followup_question(
            str(session_id),
            latest_question["id"],
            decision.question_text,
            [str(source_id) for source_id in decision.source_ids],
            source_content_snapshots={
                source["id"]: source["content"]
                for source in scoped_context["sources"]
            },
        )
        return {"should_continue": True, "question": question}
    except HTTPException:
        raise
    except Exception as error:
        _next_question_generation_error(error)


@router.post(
    "/api/interviews/sessions/{session_id}/questions/{question_id}/answers",
    response_model=InterviewAnswerResponse,
    status_code=201,
)
def submit_answer(
    session_id: UUID,
    question_id: UUID,
    request: AnswerCreateRequest,
    user=Depends(require_employee),
):
    _owned_session(session_id, user["sub"])
    question = knowledge_store.get_interview_question(str(question_id))
    if question is None or question["session_id"] != str(session_id):
        raise HTTPException(status_code=404, detail="Interview question not found.")
    try:
        answer = knowledge_store.record_interview_answer(
            str(question_id), request.answer_text
        )
    except knowledge_store.InterviewSessionConflictError as error:
        _conflict(error)
    except knowledge_store.InterviewQuestionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"answer": answer}


@router.post("/api/interviews/sessions/{session_id}/complete", response_model=InterviewSessionResponse)
def complete_session(session_id: UUID, user=Depends(require_employee)):
    session = _owned_session(session_id, user["sub"])
    try:
        if session["status"] != "completed":
            knowledge_store.update_interview_session_status(
                str(session_id), "completed"
            )
    except knowledge_store.InterviewSessionConflictError as error:
        _conflict(error)
    except knowledge_store.InterviewSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"session": _session_details(session_id)}


@router.post(
    "/api/interviews/sessions/{session_id}/analysis",
    response_model=InterviewAnalysisRecordResponse,
)
def create_analysis(session_id: UUID, user=Depends(require_employee)):
    session = _owned_session(session_id, user["sub"])
    if session["status"] != "completed":
        raise HTTPException(
            status_code=409,
            detail="Interview analysis requires a completed session.",
        )
    try:
        return _generate_analysis_once(session_id)
    except (
        InterviewAnalysisContextError,
        InterviewAnalysisProviderError,
        InterviewAnalysisValidationError,
    ) as error:
        _analysis_generation_error(error)
    except Exception as error:
        _analysis_generation_error(error)


@router.get(
    "/api/interviews/sessions/{session_id}/analysis",
    response_model=InterviewAnalysisHistoryResponse,
)
def get_analysis_history(session_id: UUID, user=Depends(require_employee)):
    _owned_session(session_id, user["sub"])
    history = knowledge_store.get_interview_analyses(str(session_id))
    return {
        "session_id": str(session_id),
        "latest": history[-1] if history else None,
        "history": history,
    }
