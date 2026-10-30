
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, APIConnectionError, APIStatusError, AuthenticationError

# Resolve paths relative to this Python file
BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent

# Load environment variables from backend/.env
load_dotenv(BACKEND_DIR / ".env")

# Check API key
api_key = os.getenv("OPENAI_API_KEY")

if not api_key:
    raise SystemExit(
        "ERROR: OPENAI_API_KEY not found.\n"
        f"Check that your API key is set in: {BACKEND_DIR / '.env'}"
    )

# Audio file is expected in the project root
audio_path = PROJECT_ROOT / "sample.mp3"

if not audio_path.is_file():
    raise SystemExit(
        f"ERROR: Audio file not found: {audio_path}\n"
        "Place sample.mp3 in the Helix_AI_V2 project root."
    )

try:
    client = OpenAI(api_key=api_key)

    print(f"Transcribing: {audio_path.name}")
    print("Please wait...")

    with audio_path.open("rb") as audio_file:
        result = client.audio.transcriptions.create(
            model="gpt-transcribe",
            file=audio_file,
        )

    print("\n--- TRANSCRIPTION RESULT ---\n")
    print(result.text)
    print("\n--- TRANSCRIPTION COMPLETE ---")

except AuthenticationError:
    print("ERROR: Authentication failed. Check your OpenAI API key and API access.")

except APIConnectionError as exc:
    print("ERROR: Could not connect to the OpenAI API.")
    print("Check your internet connection, DNS, firewall, or required proxy.")
    print(f"Details: {exc}")

except APIStatusError as exc:
    print(f"OpenAI API returned HTTP {exc.status_code}.")
    print("Check the API error details, billing, model access, and upload limits.")
    print(exc.message)

except Exception as exc:
    print(f"Unexpected error: {type(exc).__name__}: {exc}")
