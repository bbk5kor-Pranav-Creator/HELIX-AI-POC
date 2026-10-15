import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

load_dotenv(ENV_FILE)

app = FastAPI(title="Helix AI V2 API")


class LoginRequest(BaseModel):
    email: str
    password: str
    role: str


@app.get("/")
def root():
    return {"message": "Helix AI V2 backend is running"}


@app.post("/api/auth/login")
def login(request: LoginRequest):
    employee_email = os.getenv("EMPLOYEE_EMAIL")
    employee_password = os.getenv("EMPLOYEE_PASSWORD")

    if not employee_email or not employee_password:
        raise HTTPException(
            status_code=500,
            detail="Authentication configuration is missing.",
        )

    if request.role != "Employee":
        raise HTTPException(
            status_code=403,
            detail="This workspace role is not configured yet.",
        )

    if (
        request.email.strip().lower() == employee_email.strip().lower()
        and request.password == employee_password
    ):
        return {
            "success": True,
            "role": "Employee",
            "message": "Login successful",
        }

    raise HTTPException(
        status_code=401,
        detail="Invalid corporate email or password.",
    )