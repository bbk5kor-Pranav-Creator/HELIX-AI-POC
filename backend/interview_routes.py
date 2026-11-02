"""Employee-owned AI Interview Studio session APIs."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

import knowledge_store
from knowledge_routes import require_employee


router = APIRouter(prefix="/api/interviews/sessions", tags=["Interviews"])
SessionStatus = Literal["created", "in_progress", "completed", "failed"]


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


@router.post("", response_model=InterviewSessionResponse, status_code=201)
def create_session(
    _request: SessionCreateRequest = Body(default=SessionCreateRequest()),
    user=Depends(require_employee),
):
    session = knowledge_store.create_interview_session(user["sub"])
    return {"session": knowledge_store.get_interview_session_details(session["id"])}


@router.get("", response_model=InterviewSessionListResponse)
def list_sessions(user=Depends(require_employee)):
    return {"sessions": knowledge_store.list_interview_sessions(user["sub"])}


@router.get("/{session_id}", response_model=InterviewSessionResponse)
def get_session(session_id: UUID, user=Depends(require_employee)):
    _owned_session(session_id, user["sub"])
    return {"session": _session_details(session_id)}


@router.post(
    "/{session_id}/questions",
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
    "/{session_id}/questions/{question_id}/answers",
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


@router.post("/{session_id}/complete", response_model=InterviewSessionResponse)
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
