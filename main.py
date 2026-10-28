import os, json, time, uuid, sqlite3, mimetypes
from pathlib import Path
from typing import Optional
import requests
from PIL import Image
from io import BytesIO
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')
API_KEY = os.getenv('YOUCAM_API_KEY', '').strip()
API_BASE = os.getenv('YOUCAM_API_BASE', 'https://yce-api-01.makeupar.com').rstrip('/')
DB_PATH = BASE_DIR / 'groomai.db'
MAX_BYTES = 9 * 1024 * 1024

app = FastAPI(title='GroomAI', version='1.0.0')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=False, allow_methods=['*'], allow_headers=['*'])
app.mount('/static', StaticFiles(directory=BASE_DIR / 'static'), name='static')

HAIR_ENDPOINT = '/s2s/v2.1/task/hair-transfer'
HAIR_TEMPLATE_ENDPOINT = '/s2s/v2.1/task/template/hair-transfer'
BEARD_ENDPOINT = '/s2s/v2.0/task/beard-style'
BEARD_TEMPLATE_ENDPOINT = '/s2s/v2.0/task/template/beard-style'
FILE_ENDPOINT = '/s2s/v2.0/file'


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS feedback (
            id TEXT PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            style_type TEXT, style_name TEXT, satisfaction INTEGER, accuracy INTEGER,
            notes TEXT, preferred_maintenance TEXT
        )''')
        con.execute('''CREATE TABLE IF NOT EXISTS saved_looks (
            id TEXT PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            style_type TEXT, style_name TEXT, source_image TEXT, result_url TEXT,
            notes TEXT
        )''')

init_db()


def headers():
    if not API_KEY:
        raise HTTPException(503, 'YouCam API key is not configured. Add YOUCAM_API_KEY to your .env file and restart GroomAI.')
    return {'Authorization': f'Bearer {API_KEY}'}


def data_obj(payload):
    return payload.get('data') if isinstance(payload, dict) and isinstance(payload.get('data'), dict) else payload


def youcam_request(method: str, endpoint: str, **kwargs):
    try:
        response = requests.request(method, f'{API_BASE}{endpoint}', headers={**headers(), **kwargs.pop('headers', {})}, timeout=45, **kwargs)
    except requests.RequestException as exc:
        raise HTTPException(502, f'Could not reach YouCam API: {exc}')
    try:
        payload = response.json()
    except ValueError:
        payload = {'raw': response.text[:1000]}
    if not response.ok:
        message = payload.get('error') or payload.get('message') or payload.get('error_code') or f'YouCam API returned HTTP {response.status_code}'
        raise HTTPException(response.status_code if response.status_code < 500 else 502, str(message))
    return payload


def extract_list(payload):
    obj = data_obj(payload)
    if isinstance(obj, list):
        return obj
    if not isinstance(obj, dict):
        return []
    for key in ('templates', 'items', 'results', 'styles', 'template_list'):
        value = obj.get(key)
        if isinstance(value, list):
            return value
    for value in obj.values():
        if isinstance(value, dict):
            for key in ('templates', 'items', 'results', 'styles'):
                if isinstance(value.get(key), list):
                    return value[key]
    return []


def normalize_template(item, index):
    if not isinstance(item, dict):
        return None
    tid = item.get('template_id') or item.get('id') or item.get('style_id')
    if tid is None:
        return None
    name = item.get('template_name') or item.get('name') or item.get('style_name') or item.get('title') or f'Style {index + 1}'
    image = item.get('thumbnail_url') or item.get('preview_url') or item.get('image_url') or item.get('url') or item.get('thumbnail')
    group = item.get('group_name') or item.get('category') or item.get('style_group') or 'Recommended'
    return {'id': str(tid), 'name': str(name), 'image': image, 'group': str(group)}


def upload_to_youcam(file_bytes: bytes, filename: str, content_type: str):
    if len(file_bytes) > MAX_BYTES:
        raise HTTPException(413, 'Image must be smaller than 9 MB.')
    # Normalise common phone uploads to JPEG and resize to YouCam's documented image bounds.
    # This also makes PNG/WebP uploads compatible with hair/beard endpoints that require JPEG.
    try:
        image = Image.open(BytesIO(file_bytes)).convert('RGB')
        image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(output, format='JPEG', quality=90, optimize=True)
        file_bytes = output.getvalue()
        filename = f'{Path(filename).stem or "groomai-photo"}.jpg'
    except Exception as exc:
        raise HTTPException(400, f'Could not read this image. Please try a JPG or PNG photo. ({exc})')
    ctype = 'image/jpeg'
    payload = youcam_request('POST', FILE_ENDPOINT, json={'files': [{'content_type': ctype, 'file_name': Path(filename).name, 'file_size': len(file_bytes)}]})
    obj = data_obj(payload)
    files = obj.get('files', []) if isinstance(obj, dict) else []
    if not files:
        raise HTTPException(502, f'Unexpected YouCam file upload response: {json.dumps(payload)[:600]}')
    file_info = files[0]
    file_id = file_info.get('file_id')
    reqs = file_info.get('requests') or []
    if not file_id or not reqs or not reqs[0].get('url'):
        raise HTTPException(502, 'YouCam did not return a file ID and upload URL.')
    upload_req = reqs[0]
    try:
        put_headers = upload_req.get('headers') or {'Content-Type': ctype}
        up = requests.request(upload_req.get('method', 'PUT'), upload_req['url'], headers=put_headers, data=file_bytes, timeout=60)
        up.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(502, f'Could not upload image to YouCam storage: {exc}')
    return str(file_id)


def task_result(kind: str, task_id: str):
    endpoint = HAIR_ENDPOINT if kind == 'hair' else BEARD_ENDPOINT
    payload = youcam_request('GET', f'{endpoint}/{task_id}')
    obj = data_obj(payload)
    if not isinstance(obj, dict):
        obj = {}
    result = obj.get('results') or {}
    url = result.get('url') if isinstance(result, dict) else None
    status = obj.get('task_status') or obj.get('status') or 'running'
    return {'task_id': task_id, 'task_status': status, 'result_url': url, 'error': obj.get('error'), 'raw': payload}


@app.get('/', response_class=HTMLResponse)
def index():
    return (BASE_DIR / 'static' / 'index.html').read_text(encoding='utf-8')

@app.get('/api/health')
def health():
    return {'ok': True, 'app': 'GroomAI', 'youcam_configured': bool(API_KEY), 'api_base': API_BASE}

@app.get('/api/templates/{kind}')
def templates(kind: str):
    if kind not in ('hair', 'beard'):
        raise HTTPException(404, 'Use hair or beard.')
    endpoint = HAIR_TEMPLATE_ENDPOINT if kind == 'hair' else BEARD_TEMPLATE_ENDPOINT
    # The current YouCam template endpoints support pagination. Fetch a first page for MVP.
    payload = youcam_request('GET', endpoint, params={'page_size': 20})
    items = [normalize_template(item, i) for i, item in enumerate(extract_list(payload))]
    items = [item for item in items if item]
    return {'kind': kind, 'templates': items, 'count': len(items), 'raw_count': len(extract_list(payload))}

@app.post('/api/try-on')
async def try_on(kind: str = Form(...), template_id: str = Form(...), file: UploadFile = File(...), style_name: str = Form('Selected style')):
    if kind not in ('hair', 'beard'):
        raise HTTPException(400, 'Style type must be hair or beard.')
    if not template_id.strip():
        raise HTTPException(400, 'Select a style first.')
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(400, 'The uploaded image is empty.')
    file_id = upload_to_youcam(image_bytes, file.filename or 'selfie.jpg', file.content_type or 'image/jpeg')
    endpoint = HAIR_ENDPOINT if kind == 'hair' else BEARD_ENDPOINT
    payload_body = {'src_file_id': file_id, 'template_id': template_id}
    if kind == 'hair':
        payload_body['hair_color'] = 'src'
    payload = youcam_request('POST', endpoint, json=payload_body)
    obj = data_obj(payload)
    task_id = obj.get('task_id') if isinstance(obj, dict) else None
    if not task_id:
        raise HTTPException(502, f'YouCam did not return a task ID: {json.dumps(payload)[:700]}')
    return {'task_id': str(task_id), 'kind': kind, 'template_id': template_id, 'style_name': style_name, 'message': 'Task created. Poll /api/tasks/{kind}/{task_id} for the result.'}

@app.get('/api/tasks/{kind}/{task_id}')
def get_task(kind: str, task_id: str):
    if kind not in ('hair', 'beard'):
        raise HTTPException(404, 'Use hair or beard.')
    return task_result(kind, task_id)

@app.post('/api/saved-looks')
def save_look(body: dict):
    look_id = str(uuid.uuid4())
    with db() as con:
        con.execute('INSERT INTO saved_looks(id,style_type,style_name,source_image,result_url,notes) VALUES(?,?,?,?,?,?)',
                    (look_id, body.get('style_type','hair'), body.get('style_name','Saved look'), body.get('source_image'), body.get('result_url'), body.get('notes','')))
    return {'ok': True, 'id': look_id}

@app.get('/api/saved-looks')
def list_saved():
    with db() as con:
        rows = con.execute('SELECT * FROM saved_looks ORDER BY created_at DESC LIMIT 100').fetchall()
    return {'items': [dict(row) for row in rows]}

@app.delete('/api/saved-looks/{look_id}')
def delete_saved(look_id: str):
    with db() as con:
        con.execute('DELETE FROM saved_looks WHERE id=?', (look_id,))
    return {'ok': True}

@app.post('/api/feedback')
def feedback(body: dict):
    satisfaction = int(body.get('satisfaction', 0))
    accuracy = int(body.get('accuracy', 0))
    if satisfaction < 1 or satisfaction > 5 or accuracy < 1 or accuracy > 5:
        raise HTTPException(400, 'Ratings must be from 1 to 5.')
    with db() as con:
        con.execute('INSERT INTO feedback(id,style_type,style_name,satisfaction,accuracy,notes,preferred_maintenance) VALUES(?,?,?,?,?,?,?)',
                    (str(uuid.uuid4()), body.get('style_type','hair'), body.get('style_name',''), satisfaction, accuracy, body.get('notes',''), body.get('preferred_maintenance','')))
    return {'ok': True}

@app.get('/api/feedback')
def list_feedback():
    with db() as con:
        rows = con.execute('SELECT * FROM feedback ORDER BY created_at DESC LIMIT 100').fetchall()
    return {'items': [dict(row) for row in rows]}
