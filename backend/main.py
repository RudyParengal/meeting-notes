"""FastAPI backend for meeting-notes. Port 8766."""
import os, sys, uuid, logging, asyncio, json, hashlib, secrets, tempfile, shutil
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / '.env')

from fastapi import FastAPI, Header, HTTPException, BackgroundTasks, Query, UploadFile, File
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List
from fastapi.responses import FileResponse

import pipeline, storage
from prompts import TEMPLATES

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('main')

API_KEY = os.environ['MEETING_NOTES_API_KEY']
PORT = int(os.environ.get('PORT', 8766))
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '').strip()

USERS_FILE = Path(__file__).parent / 'users.json'
_sessions: dict[str, str] = {}

# Temp dir for audio uploads
UPLOAD_DIR = Path(tempfile.gettempdir()) / 'meeting-notes-uploads'
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Whisper model for server-side transcription (B1)
_server_whisper = None


def _get_server_whisper():
    global _server_whisper
    if _server_whisper is None:
        from faster_whisper import WhisperModel
        model_dir = Path(__file__).parent / 'models' / 'base'
        model_id = str(model_dir) if model_dir.exists() else 'base'
        log.info(f'Loading server-side Whisper model: {model_id}')
        _server_whisper = WhisperModel(model_id, device='cpu', compute_type='int8')
        log.info('Server Whisper model loaded')
    return _server_whisper


def _load_users() -> list[dict]:
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text(encoding='utf-8')).get('users', [])
    return []


def _save_users(users: list[dict]):
    USERS_FILE.write_text(json.dumps({'users': users}, indent=2), encoding='utf-8')


def _find_user(email: str) -> dict | None:
    for u in _load_users():
        if u['email'] == email:
            return u
    return None


def _find_user_by_api_key(api_key: str) -> dict | None:
    for u in _load_users():
        if u.get('api_key') == api_key:
            return u
    return None


def _generate_api_key() -> str:
    return 'mn_' + secrets.token_hex(24)


app = FastAPI(title='Meeting Notes API', version='1.2')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])

_status: dict[str, str] = {}


def check_auth(x_api_key: Optional[str] = None, authorization: Optional[str] = None) -> dict:
    """Returns the authenticated user dict."""
    if x_api_key:
        user = _find_user_by_api_key(x_api_key)
        if user:
            return user
        if x_api_key == API_KEY:
            # Fallback: legacy single-key access maps to the admin account
            admin = _find_user(os.environ.get('ADMIN_EMAIL', 'admin@example.com'))
            if admin:
                return admin
    if authorization and authorization.startswith('Bearer '):
        token = authorization[7:]
        email = _sessions.get(token)
        if email:
            user = _find_user(email)
            if user:
                return user
    raise HTTPException(status_code=401, detail='invalid or missing credentials')


class TranscribeRequest(BaseModel):
    transcript: str = Field(..., min_length=10)
    duration_sec: int = Field(..., ge=0)
    timestamp: Optional[str] = None
    meeting_type: str = 'auto'
    session_id: Optional[str] = None
    hint_title: Optional[str] = None


@app.get('/health')
def health():
    return {'status': 'ok', 'ts': datetime.now(timezone.utc).isoformat()}


@app.get('/templates')
def get_templates(x_api_key: Optional[str] = Header(None),
                  authorization: Optional[str] = Header(None)):
    check_auth(x_api_key, authorization)
    return {'templates': TEMPLATES}


# -----------------------------------------------------------------
# Webhook notification (B3)
# -----------------------------------------------------------------
def _fire_webhook(session_id: str, data: dict):
    """Fire-and-forget webhook notification after processing completes."""
    if not WEBHOOK_URL:
        return
    import httpx
    try:
        payload = {
            'session_id': session_id,
            'title': storage._extract_title(data.get('summary', '')),
            'meeting_type': data.get('meeting_type', 'unknown'),
            'user_email': data.get('user_email', ''),
            'dashboard_url': os.environ.get('DASHBOARD_URL', ''),
            'timestamp': data.get('timestamp', ''),
        }
        resp = httpx.post(WEBHOOK_URL, json=payload, timeout=10)
        log.info(f'Webhook fired to {WEBHOOK_URL}: status={resp.status_code}')
    except Exception as e:
        log.warning(f'Webhook failed: {e}')


@app.post('/transcribe', status_code=202)
async def transcribe(req: TranscribeRequest, background: BackgroundTasks,
                     x_api_key: Optional[str] = Header(None),
                     authorization: Optional[str] = Header(None)):
    user = check_auth(x_api_key, authorization)
    sid = req.session_id or uuid.uuid4().hex
    ts = req.timestamp or datetime.now(timezone.utc).isoformat()
    _status[sid] = 'processing'

    initial = {
        'session_id': sid,
        'timestamp': ts,
        'duration_sec': req.duration_sec,
        'transcript': req.transcript,
        'meeting_type': req.meeting_type,
        'summary': '_Processing..._',
        'actions': '_Processing..._',
        'status': 'processing',
        'user_email': user['email'],
        'tags': [],
    }
    storage.save_session(sid, initial)

    background.add_task(_process, sid, req.transcript, req.duration_sec, ts, req.meeting_type, user['email'])
    return {'session_id': sid, 'status': 'accepted', 'poll_url': '/results/' + sid}


async def _process(session_id, transcript, duration_sec, ts, meeting_type, user_email):
    try:
        log.info('[%s] processing, %d chars, type=%s', session_id, len(transcript), meeting_type)
        result = await pipeline.process_transcript(transcript, duration_sec, ts, meeting_type)
        existing = storage.load_session(session_id) or {}
        data = {
            'session_id': session_id,
            'timestamp': ts,
            'duration_sec': duration_sec,
            'transcript': transcript,
            'meeting_type': result['meeting_type'],
            'summary': result['summary'],
            'actions': result['actions'],
            'timing': result['timing'],
            'status': 'complete',
            'user_email': user_email,
            'tags': existing.get('tags', []),
        }
        storage.save_session(session_id, data)
        _status[session_id] = 'complete'
        log.info('[%s] done in %ss', session_id, result['timing']['total_sec'])
        # Fire webhook (B3)
        _fire_webhook(session_id, data)
    except Exception as e:
        log.exception('[%s] failed', session_id)
        _status[session_id] = 'failed: ' + str(e)
        existing = storage.load_session(session_id) or {}
        existing['status'] = 'failed'
        existing['error'] = str(e)
        storage.save_session(session_id, existing)


# -----------------------------------------------------------------
# Audio file upload + server-side transcription (B1)
# -----------------------------------------------------------------
ALLOWED_AUDIO_TYPES = {'.wav', '.mp3', '.m4a', '.ogg', '.webm', '.flac', '.aac'}


@app.post('/upload-audio', status_code=202)
async def upload_audio(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    meeting_type: str = Query('auto'),
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Accept an audio file upload, transcribe server-side with faster-whisper, then process."""
    user = check_auth(x_api_key, authorization)

    # Validate file extension
    ext = Path(file.filename or 'audio.wav').suffix.lower()
    if ext not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(400, f'Unsupported audio format: {ext}. Allowed: {ALLOWED_AUDIO_TYPES}')

    # Save uploaded file
    sid = uuid.uuid4().hex
    audio_path = UPLOAD_DIR / f'{sid}{ext}'
    try:
        with open(audio_path, 'wb') as f:
            shutil.copyfileobj(file.file, f)
        file_size = audio_path.stat().st_size
        log.info(f'[{sid}] Received audio upload: {file.filename} ({file_size} bytes)')
    except Exception as e:
        raise HTTPException(500, f'Failed to save upload: {e}')

    ts = datetime.now(timezone.utc).isoformat()
    _status[sid] = 'transcribing'

    initial = {
        'session_id': sid,
        'timestamp': ts,
        'duration_sec': 0,
        'transcript': '',
        'meeting_type': meeting_type,
        'summary': '_Transcribing audio..._',
        'actions': '_Transcribing audio..._',
        'status': 'transcribing',
        'user_email': user['email'],
        'tags': [],
    }
    storage.save_session(sid, initial)

    background.add_task(_transcribe_and_process, sid, str(audio_path), ts, meeting_type, user['email'])
    return {'session_id': sid, 'status': 'transcribing', 'poll_url': f'/results/{sid}'}


async def _transcribe_and_process(session_id, audio_path, ts, meeting_type, user_email):
    """Server-side transcription + full pipeline."""
    try:
        log.info('[%s] server-side transcription starting', session_id)
        model = _get_server_whisper()

        # Transcribe
        segments, info = model.transcribe(audio_path, beam_size=5, vad_filter=True)
        lines = []
        for seg in segments:
            t = int(seg.start)
            mins, secs = divmod(t, 60)
            lines.append(f'[{mins:02d}:{secs:02d}] {seg.text.strip()}')
        transcript = '\n'.join(lines)
        duration_sec = int(info.duration) if hasattr(info, 'duration') else 0

        log.info('[%s] transcription done: %d chars, %ds', session_id, len(transcript), duration_sec)

        if len(transcript.strip()) < 20:
            existing = storage.load_session(session_id) or {}
            existing['status'] = 'failed'
            existing['error'] = 'No speech detected in audio'
            storage.save_session(session_id, existing)
            _status[session_id] = 'failed: no speech'
            return

        # Update with transcript, then run normal pipeline
        _status[session_id] = 'processing'
        await _process(session_id, transcript, duration_sec, ts, meeting_type, user_email)

    except Exception as e:
        log.exception('[%s] transcribe+process failed', session_id)
        _status[session_id] = 'failed: ' + str(e)
        existing = storage.load_session(session_id) or {}
        existing['status'] = 'failed'
        existing['error'] = str(e)
        storage.save_session(session_id, existing)
    finally:
        # Clean up uploaded file
        try:
            os.unlink(audio_path)
        except Exception:
            pass


# -----------------------------------------------------------------
# PDF export (B5)
# -----------------------------------------------------------------
@app.get('/results/{session_id}/pdf')
def get_pdf(session_id: str,
            x_api_key: Optional[str] = Header(None),
            authorization: Optional[str] = Header(None)):
    """Export meeting notes as PDF."""
    user = check_auth(x_api_key, authorization)
    data = storage.load_session(session_id)
    if not data:
        raise HTTPException(404, 'not found')
    owner = data.get('user_email', storage.ADMIN_EMAIL)
    if user['role'] != 'admin' and user['email'] != owner:
        raise HTTPException(403, 'forbidden')

    md_text = storage.get_markdown(session_id)
    if not md_text:
        raise HTTPException(404, 'markdown not found')

    try:
        import markdown
        from weasyprint import HTML

        # Convert markdown to HTML
        html_body = markdown.markdown(md_text, extensions=['tables', 'fenced_code'])
        full_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       max-width: 800px; margin: 40px auto; padding: 0 20px; color: #333; line-height: 1.6; }}
h1 {{ color: #1a1a2e; border-bottom: 2px solid #4361ee; padding-bottom: 8px; }}
h2 {{ color: #2d3436; margin-top: 24px; }}
code {{ background: #f5f5f5; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }}
pre {{ background: #f5f5f5; padding: 16px; border-radius: 6px; overflow-x: auto; }}
details {{ margin-top: 16px; }}
summary {{ cursor: pointer; font-weight: bold; }}
hr {{ border: none; border-top: 1px solid #ddd; margin: 24px 0; }}
</style></head><body>{html_body}</body></html>"""

        pdf_bytes = HTML(string=full_html).write_pdf()
        title = storage._extract_title(data.get('summary', ''))
        safe_title = ''.join(c for c in title if c.isalnum() or c in ' -_')[:40] or 'meeting'

        return Response(
            content=pdf_bytes,
            media_type='application/pdf',
            headers={'Content-Disposition': f'attachment; filename="meeting-{safe_title}-{session_id[:8]}.pdf"'}
        )
    except Exception as e:
        log.exception('PDF generation failed')
        raise HTTPException(500, f'PDF generation failed: {e}')


@app.post('/results/{session_id}/reprocess', status_code=202)
async def reprocess_session(session_id: str, background: BackgroundTasks,
                            meeting_type: str = Query(...),
                            x_api_key: Optional[str] = Header(None),
                            authorization: Optional[str] = Header(None)):
    user = check_auth(x_api_key, authorization)
    data = storage.load_session(session_id)
    if not data:
        raise HTTPException(404, 'not found')
    owner = data.get('user_email', storage.ADMIN_EMAIL)
    if user['role'] != 'admin' and user['email'] != owner:
        raise HTTPException(403, 'forbidden')
    data['status'] = 'processing'
    data['meeting_type'] = meeting_type
    data['summary'] = '_Re-processing..._'
    data['actions'] = '_Re-processing..._'
    storage.save_session(session_id, data)
    _status[session_id] = 'processing'
    background.add_task(_process, session_id, data['transcript'], data['duration_sec'],
                        data['timestamp'], meeting_type, data.get('user_email', owner))
    return {'session_id': session_id, 'status': 'reprocessing'}


@app.get('/results/{session_id}')
def get_results(session_id: str, x_api_key: Optional[str] = Header(None),
                authorization: Optional[str] = Header(None)):
    user = check_auth(x_api_key, authorization)
    data = storage.load_session(session_id)
    if not data:
        raise HTTPException(404, 'not found')
    owner = data.get('user_email', storage.ADMIN_EMAIL)
    if user['role'] != 'admin' and user['email'] != owner:
        raise HTTPException(403, 'forbidden')
    return data


@app.get('/results/{session_id}/markdown', response_class=PlainTextResponse)
def get_markdown(session_id: str, x_api_key: Optional[str] = Header(None),
                 authorization: Optional[str] = Header(None)):
    user = check_auth(x_api_key, authorization)
    data = storage.load_session(session_id)
    if data:
        owner = data.get('user_email', storage.ADMIN_EMAIL)
        if user['role'] != 'admin' and user['email'] != owner:
            raise HTTPException(403, 'forbidden')
    md = storage.get_markdown(session_id)
    if not md:
        raise HTTPException(404, 'not found')
    return md


@app.put('/results/{session_id}/tags')
def update_tags(session_id: str, body: dict,
                x_api_key: Optional[str] = Header(None),
                authorization: Optional[str] = Header(None)):
    user = check_auth(x_api_key, authorization)
    data = storage.load_session(session_id)
    if not data:
        raise HTTPException(404, 'not found')
    owner = data.get('user_email', storage.ADMIN_EMAIL)
    if user['role'] != 'admin' and user['email'] != owner:
        raise HTTPException(403, 'forbidden')
    tags = body.get('tags', [])
    if not isinstance(tags, list):
        raise HTTPException(400, 'tags must be a list of strings')
    tags = [str(t).strip().lower() for t in tags if str(t).strip()]
    data['tags'] = tags
    storage.save_session(session_id, data)
    return {'session_id': session_id, 'tags': tags}


@app.get('/sessions')
def list_sessions(limit: int = Query(100, ge=1, le=500),
                  search: Optional[str] = None,
                  tag: Optional[str] = None,
                  show_all: Optional[bool] = Query(False, alias='all'),
                  x_api_key: Optional[str] = Header(None),
                  authorization: Optional[str] = Header(None)):
    user = check_auth(x_api_key, authorization)
    is_all = show_all and user['role'] == 'admin'
    return {'sessions': storage.list_sessions(
        limit=limit, search=search,
        user_email=user['email'], show_all=is_all,
        tag=tag
    )}


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post('/login')
def login(req: LoginRequest):
    user = _find_user(req.email)
    if not user:
        raise HTTPException(401, 'invalid credentials')
    pw_hash = hashlib.sha256(req.password.encode()).hexdigest()
    if pw_hash != user['password_hash']:
        raise HTTPException(401, 'invalid credentials')
    token = secrets.token_hex(32)
    _sessions[token] = req.email
    return {'token': token, 'email': user['email'], 'name': user.get('name', ''),
            'role': user.get('role', 'member')}


@app.get('/me')
def me(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'not authenticated')
    token = authorization[7:]
    email = _sessions.get(token)
    if not email:
        raise HTTPException(401, 'invalid session')
    user = _find_user(email)
    if not user:
        raise HTTPException(401, 'user not found')
    return {'email': user['email'], 'name': user.get('name', ''), 'role': user.get('role', 'member')}


@app.post('/logout')
def logout(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith('Bearer '):
        token = authorization[7:]
        _sessions.pop(token, None)
    return {'status': 'ok'}


class CreateUserRequest(BaseModel):
    email: str
    password: str
    name: str


@app.post('/admin/users')
def create_user(req: CreateUserRequest,
                x_api_key: Optional[str] = Header(None),
                authorization: Optional[str] = Header(None)):
    user = check_auth(x_api_key, authorization)
    if user.get('role') != 'admin':
        raise HTTPException(403, 'admin only')
    if _find_user(req.email):
        raise HTTPException(409, 'user already exists')
    users = _load_users()
    new_user = {
        'email': req.email,
        'password_hash': hashlib.sha256(req.password.encode()).hexdigest(),
        'name': req.name,
        'role': 'member',
        'api_key': _generate_api_key(),
    }
    users.append(new_user)
    _save_users(users)
    log.info('Created user: %s', req.email)
    return {'email': new_user['email'], 'name': new_user['name'], 'role': new_user['role'],
            'api_key': new_user['api_key']}


@app.get('/admin/users')
def list_users(x_api_key: Optional[str] = Header(None),
               authorization: Optional[str] = Header(None)):
    user = check_auth(x_api_key, authorization)
    if user.get('role') != 'admin':
        raise HTTPException(403, 'admin only')
    users = _load_users()
    return {'users': [{'email': u['email'], 'name': u.get('name', ''), 'role': u.get('role', 'member'),
                       'api_key': u.get('api_key', '')} for u in users]}


@app.delete('/admin/users/{email}')
def delete_user(email: str,
                x_api_key: Optional[str] = Header(None),
                authorization: Optional[str] = Header(None)):
    user = check_auth(x_api_key, authorization)
    if user.get('role') != 'admin':
        raise HTTPException(403, 'admin only')
    if email == user['email']:
        raise HTTPException(400, 'cannot delete yourself')
    users = _load_users()
    orig_len = len(users)
    users = [u for u in users if u['email'] != email]
    if len(users) == orig_len:
        raise HTTPException(404, 'user not found')
    _save_users(users)
    log.info('Deleted user: %s', email)
    return {'status': 'deleted', 'email': email}


@app.get('/results/{session_id}/download')
def download_markdown(session_id: str,
                      x_api_key: Optional[str] = Header(None),
                      authorization: Optional[str] = Header(None)):
    user = check_auth(x_api_key, authorization)
    data = storage.load_session(session_id)
    if data:
        owner = data.get('user_email', storage.ADMIN_EMAIL)
        if user['role'] != 'admin' and user['email'] != owner:
            raise HTTPException(403, 'forbidden')
    md = storage.get_markdown(session_id)
    if not md:
        raise HTTPException(404, 'not found')
    return Response(
        content=md,
        media_type='text/markdown',
        headers={'Content-Disposition': 'attachment; filename="meeting-' + session_id[:8] + '.md"'}
    )


# -----------------------------------------------------------------
# Trash / delete / restore endpoints
# -----------------------------------------------------------------
@app.delete('/results/{session_id}')
def delete_result(session_id: str,
                  x_api_key: Optional[str] = Header(None),
                  authorization: Optional[str] = Header(None)):
    """Soft-delete a session (move to trash)."""
    user = check_auth(x_api_key, authorization)
    data = storage.load_session(session_id)
    if not data:
        raise HTTPException(404, 'not found')
    is_admin = user.get('role') == 'admin'
    try:
        ok = storage.delete_session(session_id, user['email'], is_admin)
    except PermissionError:
        raise HTTPException(403, 'forbidden')
    if not ok:
        raise HTTPException(404, 'not found')
    _status.pop(session_id, None)
    return {'status': 'trashed', 'session_id': session_id}


@app.post('/results/{session_id}/restore')
def restore_result(session_id: str,
                   x_api_key: Optional[str] = Header(None),
                   authorization: Optional[str] = Header(None)):
    """Restore a trashed session back to active list."""
    user = check_auth(x_api_key, authorization)
    data = storage.load_trashed_session(session_id)
    if not data:
        raise HTTPException(404, 'not in trash')
    is_admin = user.get('role') == 'admin'
    try:
        ok = storage.restore_session(session_id, user['email'], is_admin)
    except PermissionError:
        raise HTTPException(403, 'forbidden')
    if not ok:
        raise HTTPException(404, 'not in trash')
    return {'status': 'restored', 'session_id': session_id}


@app.delete('/results/{session_id}/purge')
def purge_result(session_id: str,
                 x_api_key: Optional[str] = Header(None),
                 authorization: Optional[str] = Header(None)):
    """Permanently delete a session that is already in the trash."""
    user = check_auth(x_api_key, authorization)
    data = storage.load_trashed_session(session_id)
    if not data:
        raise HTTPException(404, 'not in trash')
    is_admin = user.get('role') == 'admin'
    try:
        ok = storage.purge_session(session_id, user['email'], is_admin)
    except PermissionError:
        raise HTTPException(403, 'forbidden')
    if not ok:
        raise HTTPException(404, 'not in trash')
    return {'status': 'purged', 'session_id': session_id}


@app.get('/trash')
def get_trash(show_all: Optional[bool] = Query(False, alias='all'),
              x_api_key: Optional[str] = Header(None),
              authorization: Optional[str] = Header(None)):
    """List items currently in the trash for the current user.
    Admin can pass ?all=true to see everyone's trash."""
    user = check_auth(x_api_key, authorization)
    is_all = bool(show_all) and user.get('role') == 'admin'
    return {'sessions': storage.list_trash(user_email=user['email'], show_all=is_all)}


@app.get('/trash/{session_id}')
def get_trashed_detail(session_id: str,
                       x_api_key: Optional[str] = Header(None),
                       authorization: Optional[str] = Header(None)):
    """Fetch the full content of a trashed session (for the detail view)."""
    user = check_auth(x_api_key, authorization)
    data = storage.load_trashed_session(session_id)
    if not data:
        raise HTTPException(404, 'not in trash')
    owner = data.get('user_email', storage.ADMIN_EMAIL)
    if user['role'] != 'admin' and user['email'] != owner:
        raise HTTPException(403, 'forbidden')
    return data


DASHBOARD = Path(__file__).parent.parent / 'dashboard' / 'index.html'

@app.get('/', response_class=HTMLResponse)
def root():
    if DASHBOARD.exists():
        return HTMLResponse(DASHBOARD.read_text(encoding='utf-8'))
    return HTMLResponse('<h1>Meeting Notes API</h1><p>Dashboard not installed</p>')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run('main:app', host=os.environ.get('HOST', '0.0.0.0'), port=PORT, log_level='info')
