import os
from urllib.parse import urlsplit

from fastapi import HTTPException
from openai import DefaultHttpxClient


def groq_http_options() -> dict:
    proxy = os.getenv("GROQ_PROXY_URL", "").strip()
    if not proxy:
        return {}
    try:
        parsed = urlsplit(proxy)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        parsed.port
    except ValueError:
        raise HTTPException(status_code=503, detail="GROQ_PROXY_URL must be a valid HTTP(S) proxy URL.") from None
    return {"http_client": DefaultHttpxClient(proxy=proxy, trust_env=False)}
