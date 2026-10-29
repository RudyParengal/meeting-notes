"""Meeting Notes API.

    POST /transcribe        a transcript in -> 202, processed in the background
    GET  /results/{id}      status + summary + action items + transcript
    GET  /sessions          list (yours, or everyone's if you are admin)
    GET  /                  the dashboard

Authentication is ALWAYS required, with no "trusted network" switch. Either:
    X-API-Key: <master key or a user's key>        (the recorder uses this)
    Authorization: Bearer <login token>            (the dashboard uses this)
"""
import asyncio
import logging
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import (BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Path as PathParam,
                     Query, Request, UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config
import pipeline
import storage
import users
from prompts import TEMPLATES
from security import (SESSION_ID_PATTERN, LoginThrottle, TokenStore, hash_password, keys_equal,
                      verify_password)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

# Fail now, loudly, on a bad configuration - not on the first real meeting.
config.validate_for_serving()
API_KEY: str = config.load_api_key()
storage.init()
pipeline.init()
users.migrate()

DASHBOARD_DIR = config.REPO / "dashboard"
UPLOAD_DIR = config.DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_AUDIO_TYPES = {".wav", ".mp3", ".m4a", ".ogg", ".webm", ".flac", ".aac"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")

tokens = TokenStore(config.TOKEN_TTL_HOURS)
throttle = LoginThrottle(config.LOGIN_MAX_FAILURES, config.LOGIN_WINDOW_SEC)
# Verified against when an account does not exist, so a wrong email and a wrong
# password take the same time and cannot be told apart.
_DUMMY_HASH = hash_password("not-a-real-password")

app = FastAPI(title="Meeting Notes API", version=config.__version__,
              docs_url=None, redoc_url=None, openapi_url=None)   # no public schema browser

if config.CORS_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS,
                       allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Authorization", "Content-Type", "X-API-Key"])

SessionId = PathParam(..., pattern=SESSION_ID_PATTERN)

CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
       "form-action 'self'; frame-ancestors 'none'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if not request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store"     # meeting content must not be cached
    if request.url.path == "/":
        resp.headers["Content-Security-Policy"] = CSP
    return resp


@app.exception_handler(users.UsersFileCorrupt)
async def users_file_corrupt(request: Request, exc: users.UsersFileCorrupt):
    """Mid-run, a damaged users.json must not take the whole server down."""
    log.error("%s", exc)
    return JSONResponse(status_code=503, content={
        "detail": "The user list is damaged. An administrator must fix users.json "
                  "(it has not been changed)."})


# ------------------------------------------------------------------ auth ---
def client_ip(request: Request) -> str:
    if config.TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[-1].strip()      # the address our own proxy saw
    return request.client.host if request.client else "unknown"


def current_user(x_api_key: Optional[str] = Header(None),
                 authorization: Optional[str] = Header(None)) -> dict:
    if x_api_key:
        if keys_equal(x_api_key, API_KEY):
            return users.admin_user()
        user = users.find_by_api_key(x_api_key)
        if user:
            return user
    if authorization and authorization.startswith("Bearer "):
        email = tokens.lookup(authorization[7:])
        if email:
            user = users.find_by_email(email)
            if user:
                return user
    raise HTTPException(401, "invalid or missing credentials")


def admin_only(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(403, "admin only")
    return user


def _is_admin(user: dict) -> bool:
    return user.get("role") == "admin"


def _owned(user: dict, data: dict) -> None:
    if not _is_admin(user) and data.get("user_email", storage.ADMIN_EMAIL) != user["email"]:
        raise HTTPException(403, "forbidden")


def _load_owned(session_id: str, user: dict) -> dict:
    data = storage.load_session(session_id)
    if not data:
        raise HTTPException(404, "not found")
    _owned(user, data)
    return data


# ---------------------------------------------------------------- basics ---
@app.get("/health")
def health():
    """Liveness only. Says nothing about configuration - it is public."""
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/health/deep")
def health_deep(user: dict = Depends(current_user)):
    """What `scripts/doctor.py` checks: is this install actually able to work."""
    writable = True
    try:
        probe = storage.NOTES_DIR / ".write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError:
        writable = False
    return {"status": "ok" if writable else "degraded", "version": config.__version__,
            "storage_writable": writable,
            "llm": {k: {"model": s.model, "base_url": s.base_url} for k, s in pipeline.stages().items()},
            "audio_upload": _have("faster_whisper"), "pdf_export": _have("weasyprint")}


def _have(module: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(module) is not None


@app.get("/templates")
def get_templates(user: dict = Depends(current_user)):
    return {"templates": TEMPLATES}


# --------------------------------------------------------------- ingest ---
class TranscribeRequest(BaseModel):
    transcript: str = Field(..., min_length=10, max_length=config.MAX_TRANSCRIPT_CHARS)
    duration_sec: int = Field(..., ge=0)
    timestamp: Optional[str] = None
    meeting_type: str = Field("auto", max_length=32)
    session_id: Optional[str] = Field(None, pattern=SESSION_ID_PATTERN)
    hint_title: Optional[str] = Field(None, max_length=200)


def _parse_ts(ts: Optional[str]) -> str:
    if not ts:
        return datetime.now(timezone.utc).isoformat()
    try:
        datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, "timestamp must be ISO-8601, e.g. 2026-09-04T13:43:00+00:00")
    return ts


@app.post("/transcribe", status_code=202)
async def transcribe(req: TranscribeRequest, background: BackgroundTasks,
                     user: dict = Depends(current_user)):
    sid = req.session_id or uuid.uuid4().hex
    ts = _parse_ts(req.timestamp)
    existing = storage.load_session(sid)
    if existing:
        _owned(user, existing)      # re-sending an id you own is fine; someone else's is not
    storage.save_session(sid, {
        "session_id": sid, "timestamp": ts, "duration_sec": req.duration_sec,
        "transcript": req.transcript, "meeting_type": req.meeting_type,
        "summary": "_Processing..._", "actions": "_Processing..._",
        "status": "processing", "user_email": user["email"], "tags": [],
    })
    background.add_task(_process, sid, req.transcript, req.duration_sec, ts,
                        req.meeting_type, user["email"])
    return {"session_id": sid, "status": "accepted", "poll_url": "/results/" + sid}


async def _fail(session_id: str, why: str) -> None:
    existing = storage.load_session(session_id) or {}
    if existing:
        existing.update(status="failed", error=why)
        storage.save_session(session_id, existing)


async def _process(session_id, transcript, duration_sec, ts, meeting_type, user_email):
    try:
        log.info("[%s] processing, %d chars, type=%s", session_id, len(transcript), meeting_type)
        result = await pipeline.process_transcript(transcript, duration_sec, ts, meeting_type)
        existing = storage.load_session(session_id) or {}
        data = {
            "session_id": session_id, "timestamp": ts, "duration_sec": duration_sec,
            "transcript": transcript, "meeting_type": result["meeting_type"],
            "summary": result["summary"], "actions": result["actions"], "timing": result["timing"],
            "status": "complete", "user_email": user_email, "tags": existing.get("tags", []),
        }
        storage.save_session(session_id, data)
        log.info("[%s] done in %ss", session_id, result["timing"]["total_sec"])
        await asyncio.to_thread(_fire_webhook, session_id, data)
    except Exception as e:
        log.exception("[%s] failed", session_id)
        await _fail(session_id, str(e))


def _fire_webhook(session_id: str, data: dict) -> None:
    """Optional: tell another system a note is ready. Fire-and-forget."""
    if not config.WEBHOOK_URL:
        return
    if not config.WEBHOOK_URL.startswith(("http://", "https://")):
        log.warning("WEBHOOK_URL ignored: must start with http:// or https://")
        return
    import httpx
    try:
        resp = httpx.post(config.WEBHOOK_URL, timeout=10, json={
            "session_id": session_id, "title": storage._extract_title(data.get("summary", "")),
            "meeting_type": data.get("meeting_type", "unknown"), "user_email": data.get("user_email", ""),
            "dashboard_url": config.PUBLIC_URL, "timestamp": data.get("timestamp", "")})
        log.info("webhook -> %s", resp.status_code)
    except Exception as e:
        log.warning("webhook failed: %s", e)


# -------------------------------------------------- audio upload (optional) ---
_whisper = None


def _get_whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        name = config.env("SERVER_WHISPER_MODEL", "base")
        log.info("loading server-side Whisper model: %s", name)
        _whisper = WhisperModel(name, device="cpu", compute_type="int8")
    return _whisper


def _transcribe_file(path: str) -> tuple[str, int]:
    segments, info = _get_whisper().transcribe(path, beam_size=5, vad_filter=True)
    lines = []
    for seg in segments:
        mins, secs = divmod(int(seg.start), 60)
        lines.append(f"[{mins:02d}:{secs:02d}] {seg.text.strip()}")
    return "\n".join(lines), int(getattr(info, "duration", 0) or 0)


@app.post("/upload-audio", status_code=202)
async def upload_audio(background: BackgroundTasks, file: UploadFile = File(...),
                       meeting_type: str = Query("auto", max_length=32),
                       user: dict = Depends(current_user)):
    """Upload a recording and let the server transcribe it. Needs faster-whisper."""
    if not _have("faster_whisper"):
        raise HTTPException(501, "Server-side transcription is not installed. "
                                 "Run: pip install -r backend/requirements-optional.txt")
    ext = Path(file.filename or "audio.wav").suffix.lower()
    if ext not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(400, f"Unsupported audio format {ext}. Allowed: {sorted(ALLOWED_AUDIO_TYPES)}")

    sid = uuid.uuid4().hex
    audio_path = UPLOAD_DIR / f"{sid}{ext}"
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    try:
        with open(audio_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > limit:
                    raise HTTPException(413, f"File larger than {config.MAX_UPLOAD_MB} MB")
                out.write(chunk)
    except HTTPException:
        audio_path.unlink(missing_ok=True)
        raise
    except Exception as e:
        audio_path.unlink(missing_ok=True)
        raise HTTPException(500, f"Failed to save upload: {e}")

    ts = datetime.now(timezone.utc).isoformat()
    storage.save_session(sid, {
        "session_id": sid, "timestamp": ts, "duration_sec": 0, "transcript": "",
        "meeting_type": meeting_type, "summary": "_Transcribing audio..._",
        "actions": "_Transcribing audio..._", "status": "transcribing",
        "user_email": user["email"], "tags": []})
    background.add_task(_transcribe_and_process, sid, str(audio_path), ts, meeting_type, user["email"])
    return {"session_id": sid, "status": "transcribing", "poll_url": f"/results/{sid}"}


async def _transcribe_and_process(session_id, audio_path, ts, meeting_type, user_email):
    try:
        # Whisper runs for minutes; in a thread so the server keeps answering.
        transcript, duration_sec = await asyncio.to_thread(_transcribe_file, audio_path)
        if len(transcript.strip()) < 20:
            await _fail(session_id, "No speech detected in audio")
            return
        await _process(session_id, transcript, duration_sec, ts, meeting_type, user_email)
    except Exception as e:
        log.exception("[%s] transcribe+process failed", session_id)
        await _fail(session_id, str(e))
    finally:
        Path(audio_path).unlink(missing_ok=True)


# -------------------------------------------------------------- results ---
@app.get("/results/{session_id}")
def get_results(session_id: str = SessionId, user: dict = Depends(current_user)):
    return _load_owned(session_id, user)


@app.get("/results/{session_id}/markdown", response_class=PlainTextResponse)
def get_markdown(session_id: str = SessionId, user: dict = Depends(current_user)):
    _load_owned(session_id, user)
    md = storage.get_markdown(session_id)
    if not md:
        raise HTTPException(404, "not found")
    return md


@app.get("/results/{session_id}/download")
def download_markdown(session_id: str = SessionId, user: dict = Depends(current_user)):
    _load_owned(session_id, user)
    md = storage.get_markdown(session_id)
    if not md:
        raise HTTPException(404, "not found")
    return Response(content=md, media_type="text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="meeting-{session_id[:8]}.md"'})


def _deny_fetch(url, *a, **k):
    # A note is built from what people said on a call. If it contains an image
    # link the PDF engine would otherwise fetch it - from this server's network.
    raise ValueError("external resources are not loaded into PDFs")


@app.get("/results/{session_id}/pdf")
def get_pdf(session_id: str = SessionId, user: dict = Depends(current_user)):
    data = _load_owned(session_id, user)
    md_text = storage.get_markdown(session_id)
    if not md_text:
        raise HTTPException(404, "markdown not found")
    if not (_have("weasyprint") and _have("markdown")):
        raise HTTPException(501, "PDF export is not installed. "
                                 "Run: pip install -r backend/requirements-optional.txt")
    import markdown
    from weasyprint import HTML
    body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
    html = ("<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
            "body{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;max-width:800px;margin:40px auto;"
            "padding:0 20px;color:#333;line-height:1.6}h1{border-bottom:2px solid #4361ee;padding-bottom:8px}"
            "code,pre{background:#f5f5f5;border-radius:4px}pre{padding:16px;white-space:pre-wrap}"
            f"</style></head><body>{body}</body></html>")
    try:
        pdf = HTML(string=html, url_fetcher=_deny_fetch).write_pdf()
    except Exception as e:
        log.exception("PDF generation failed")
        raise HTTPException(500, f"PDF generation failed: {e}")
    safe = "".join(c for c in storage._extract_title(data.get("summary", "")) if c.isalnum() or c in " -_")[:40] or "meeting"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="meeting-{safe}-{session_id[:8]}.pdf"'})


@app.post("/results/{session_id}/reprocess", status_code=202)
async def reprocess_session(background: BackgroundTasks, session_id: str = SessionId,
                            meeting_type: str = Query(..., max_length=32),
                            user: dict = Depends(current_user)):
    """Re-run summary + actions from the stored transcript (e.g. after a provider outage)."""
    data = _load_owned(session_id, user)
    data.update(status="processing", meeting_type=meeting_type,
                summary="_Re-processing..._", actions="_Re-processing..._")
    storage.save_session(session_id, data)
    background.add_task(_process, session_id, data["transcript"], data["duration_sec"],
                        data["timestamp"], meeting_type, data.get("user_email", user["email"]))
    return {"session_id": session_id, "status": "reprocessing"}


@app.put("/results/{session_id}/tags")
def update_tags(body: dict, session_id: str = SessionId, user: dict = Depends(current_user)):
    data = _load_owned(session_id, user)
    tags = body.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 50:
        raise HTTPException(400, "tags must be a list of up to 50 strings")
    data["tags"] = [str(t).strip().lower()[:40] for t in tags if str(t).strip()]
    storage.save_session(session_id, data)
    return {"session_id": session_id, "tags": data["tags"]}


@app.get("/sessions")
def list_sessions(limit: int = Query(100, ge=1, le=500), search: Optional[str] = Query(None, max_length=200),
                  tag: Optional[str] = Query(None, max_length=40),
                  show_all: bool = Query(False, alias="all"), user: dict = Depends(current_user)):
    return {"sessions": storage.list_sessions(
        limit=limit, search=search, user_email=user["email"],
        show_all=show_all and _is_admin(user), tag=tag)}


# ----------------------------------------------------------------- trash ---
def _trash_call(fn, session_id: str, user: dict, missing: str, ok_body: dict):
    try:
        ok = fn(session_id, user["email"], _is_admin(user))
    except PermissionError:
        raise HTTPException(403, "forbidden")
    if not ok:
        raise HTTPException(404, missing)
    return ok_body


@app.delete("/results/{session_id}")
def delete_result(session_id: str = SessionId, user: dict = Depends(current_user)):
    """Soft delete: moved to the trash, recoverable."""
    return _trash_call(storage.delete_session, session_id, user, "not found",
                       {"status": "trashed", "session_id": session_id})


@app.post("/results/{session_id}/restore")
def restore_result(session_id: str = SessionId, user: dict = Depends(current_user)):
    return _trash_call(storage.restore_session, session_id, user, "not in trash",
                       {"status": "restored", "session_id": session_id})


@app.delete("/results/{session_id}/purge")
def purge_result(session_id: str = SessionId, user: dict = Depends(current_user)):
    """Permanent. Only works on something already in the trash."""
    return _trash_call(storage.purge_session, session_id, user, "not in trash",
                       {"status": "purged", "session_id": session_id})


@app.get("/trash")
def get_trash(show_all: bool = Query(False, alias="all"), user: dict = Depends(current_user)):
    return {"sessions": storage.list_trash(user_email=user["email"], show_all=show_all and _is_admin(user))}


@app.get("/trash/{session_id}")
def get_trashed_detail(session_id: str = SessionId, user: dict = Depends(current_user)):
    data = storage.load_trashed_session(session_id)
    if not data:
        raise HTTPException(404, "not in trash")
    _owned(user, data)
    return data


# ----------------------------------------------------------------- login ---
class LoginRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., max_length=512)


@app.post("/login")
def login(req: LoginRequest, request: Request):
    """Admin: your ADMIN_EMAIL with the master API key as the password.
    Everyone else: the email and password the admin created for them."""
    client = client_ip(request)
    email = req.email.strip().lower()
    wait = throttle.blocked(client, email)
    if wait:
        raise HTTPException(429, f"Too many failed attempts. Try again in {wait} seconds.",
                            headers={"Retry-After": str(wait)})

    user, ok = None, False
    if email == config.ADMIN_EMAIL:
        user, ok = users.admin_user(), keys_equal(req.password, API_KEY)
    else:
        user = users.find_by_email(email)
        stored = user.get("password_hash", "") if user else _DUMMY_HASH
        ok, rehash = verify_password(req.password, stored)
        ok = ok and user is not None
        if ok and rehash:
            users.upgrade_password(email, req.password)

    if not ok:
        throttle.record_failure(client, email)
        raise HTTPException(401, "invalid credentials")
    throttle.record_success(client, email)
    return {"token": tokens.issue(user["email"]), "email": user["email"],
            "name": user.get("name", ""), "role": user.get("role", "member")}


@app.get("/me")
def me(user: dict = Depends(current_user)):
    return {"email": user["email"], "name": user.get("name", ""), "role": user.get("role", "member")}


@app.post("/logout")
def logout(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        tokens.revoke(authorization[7:])
    return {"status": "ok"}


# ----------------------------------------------------------------- admin ---
class CreateUserRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=10, max_length=512)
    name: str = Field("", max_length=100)
    role: Literal["member", "admin"] = "member"


@app.post("/admin/users")
def create_user(req: CreateUserRequest, admin: dict = Depends(admin_only)):
    if not EMAIL_RE.match(req.email):
        raise HTTPException(422, "that does not look like an email address")
    try:
        user, key = users.create(req.email, req.password, req.name, req.role)
    except ValueError:
        raise HTTPException(409, "user already exists")
    log.info("created user %s (%s)", user["email"], user["role"])
    # The only time this key is ever visible. Only its hash is stored.
    return {**users.public_view(user), "api_key": key,
            "note": "Copy this key now. It cannot be shown again; rotate it if lost."}


@app.get("/admin/users")
def list_users(admin: dict = Depends(admin_only)):
    return {"users": [users.public_view(u) for u in users.all_users()]}


@app.post("/admin/users/{email}/rotate-key")
def rotate_key(email: str, admin: dict = Depends(admin_only)):
    key = users.rotate_key(email)
    if not key:
        raise HTTPException(404, "user not found")
    return {"email": email.lower(), "api_key": key,
            "note": "Copy this key now. The previous key no longer works."}


@app.delete("/admin/users/{email}")
def delete_user(email: str, admin: dict = Depends(admin_only)):
    if email.strip().lower() == admin["email"].lower():
        raise HTTPException(400, "cannot delete yourself")
    if not users.delete(email):
        raise HTTPException(404, "user not found")
    tokens.revoke_user(email.strip().lower())
    log.info("deleted user %s", email)
    return {"status": "deleted", "email": email}


# ------------------------------------------------------------- dashboard ---
app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def root():
    index = DASHBOARD_DIR / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Meeting Notes API</h1><p>Dashboard files are missing.</p>")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info",
                proxy_headers=config.TRUST_PROXY, forwarded_allow_ips="127.0.0.1" if config.TRUST_PROXY else None)
