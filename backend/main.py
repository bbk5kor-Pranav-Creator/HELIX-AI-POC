
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.getLogger().setLevel(logging.INFO)
logging.getLogger("video_extractor").setLevel(logging.INFO)

from knowledge_routes import create_access_token, router as knowledge_router
from docupedia_routes import router as docupedia_router
from audio_routes import router as audio_router
from gemini_public_link_routes import router as public_link_router
from video_routes import router as video_router


BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

load_dotenv(ENV_FILE)

app = FastAPI(title="Helix AI V2 API")
app.include_router(knowledge_router)
app.include_router(docupedia_router)
app.include_router(audio_router)
app.include_router(public_link_router)
app.include_router(video_router)


class LoginRequest(BaseModel):
    email: str
    password: str
    role: str


@app.get("/")
def root():
    return {"message": "Helix AI V2 backend is running"}


@app.post("/api/auth/login")
def login(request: LoginRequest):
    role = request.role.strip()

    if role == "Employee":
        configured_email = os.getenv("EMPLOYEE_EMAIL")
        configured_password = os.getenv("EMPLOYEE_PASSWORD")

    elif role == "Admin":
        configured_email = os.getenv("ADMIN_EMAIL")
        configured_password = os.getenv("ADMIN_PASSWORD")

    else:
        raise HTTPException(
            status_code=400,
            detail="Invalid workspace role.",
        )

    if not configured_email or not configured_password:
        raise HTTPException(
            status_code=500,
            detail=f"{role} authentication is not configured.",
        )

    email_matches = (
        request.email.strip().lower()
        == configured_email.strip().lower()
    )
    password_matches = request.password == configured_password

    if not (email_matches and password_matches):
        raise HTTPException(
            status_code=401,
            detail="Invalid corporate email or password.",
        )

    try:
        access_token = create_access_token(configured_email, role)
    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail="Session authentication is not configured.",
        ) from error

    return {
        "success": True,
        "role": role,
        "message": "Login successful",
        "access_token": access_token,
        "token_type": "bearer",
    }
