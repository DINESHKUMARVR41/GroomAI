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
ENV_PATH = BASE_DIR / '.env'
# Load the project-local .env first; also support launching uvicorn from the project
# parent directory without silently losing an existing local environment file.
load_dotenv(ENV_PATH, override=False)
if not os.getenv('YOUCAM_API_KEY', '').strip():
    load_dotenv(Path.cwd() / '.env', override=False)
API_KEY = os.getenv('YOUCAM_API_KEY', '').strip()
if API_KEY.upper().startswith(('PASTE_', 'YOUR_', 'REPLACE_', 'ADD_YOUR_')):
    API_KEY = ''
API_BASE = os.getenv('YOUCAM_API_BASE', 'https://yce-api-01.makeupar.com').rstrip('/')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '').strip()
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-3.5-flash-lite').strip()
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
        # Preserve useful provider diagnostics, including nested error objects,
        # without ever logging or returning the Authorization header.
        detail = payload.get('error') or payload.get('message') or payload.get('error_code') or payload.get('detail')
        if isinstance(detail, (dict, list)):
            detail = json.dumps(detail, ensure_ascii=False)
        if not detail:
            detail = payload.get('raw') or f'YouCam API returned HTTP {response.status_code}'
        detail = str(detail)[:900]
        raise HTTPException(response.status_code if response.status_code < 500 else 502, f'YouCam HTTP {response.status_code}: {detail}')
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
    # YouCam template catalogues commonly use `thumb` for the exact template
    # preview image and `title` / `category_name` for display metadata.
    name = (item.get('template_name') or item.get('name') or item.get('style_name')
            or item.get('title') or f'Style {index + 1}')
    image = (item.get('thumb') or item.get('thumbnail_url') or item.get('preview_url')
             or item.get('image_url') or item.get('thumbnail') or item.get('url'))
    # Some catalogue responses wrap the URL in an object.
    if isinstance(image, dict):
        image = image.get('url') or image.get('image_url') or image.get('src')
    if not isinstance(image, str) or not image.startswith(('https://', 'http://')):
        image = None
    group = (item.get('category_name') or item.get('group_name') or item.get('category')
             or item.get('style_group') or 'Recommended')
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
    result = obj.get('results') or obj.get('result') or {}
    def find_image_url(value):
        if isinstance(value, dict):
            for key in ('url', 'image_url', 'result_url', 'output_url'):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.startswith(('https://', 'http://')):
                    return candidate
            for child in value.values():
                found = find_image_url(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = find_image_url(child)
                if found:
                    return found
        return None
    url = find_image_url(result)
    status = obj.get('task_status') or obj.get('status') or 'running'
    return {'task_id': task_id, 'task_status': status, 'result_url': url, 'error': obj.get('error') or obj.get('message'), 'raw': payload}


@app.get('/', response_class=HTMLResponse)
def index():
    return (BASE_DIR / 'static' / 'index.html').read_text(encoding='utf-8')

@app.get('/api/health')
def health():
    return {'ok': True, 'app': 'GroomAI', 'youcam_configured': bool(API_KEY), 'gemini_configured': bool(GEMINI_API_KEY), 'api_base': API_BASE, 'env_file_exists': ENV_PATH.is_file(), 'env_path': str(ENV_PATH), 'config_hint': '' if API_KEY else 'YOUCAM_API_KEY is empty or missing. The project ZIP does not contain your private .env file; restore the key in the reported env_path and restart.'}

@app.get('/api/templates/{kind}')
def templates(kind: str):
    if kind not in ('hair', 'beard'):
        raise HTTPException(404, 'Use hair or beard.')
    endpoint = HAIR_TEMPLATE_ENDPOINT if kind == 'hair' else BEARD_TEMPLATE_ENDPOINT
    # YouCam documents page_size as 1..20. Fetch up to five pages (100 templates)
    # using next_token rather than sending the invalid page_size=50 request.
    all_raw = []
    starting_token = None
    seen_tokens = set()
    max_pages = 5
    for _ in range(max_pages):
        params = {'page_size': 20}
        if starting_token:
            params['starting_token'] = starting_token
        payload = youcam_request('GET', endpoint, params=params)
        obj = data_obj(payload)
        page_items = extract_list(payload)
        all_raw.extend(page_items)
        next_token = obj.get('next_token') if isinstance(obj, dict) else None
        if not next_token or next_token in seen_tokens or not page_items:
            break
        seen_tokens.add(next_token)
        starting_token = next_token
    items = [normalize_template(item, i) for i, item in enumerate(all_raw)]
    items = [item for item in items if item]
    return {'kind': kind, 'templates': items, 'count': len(items), 'raw_count': len(all_raw)}

@app.post('/api/recommendations')
async def recommend_styles(file: UploadFile = File(...), kind: str = Form(...), templates_json: str = Form(...), preferences_json: str = Form('{}')):
    """Use Gemini vision to recommend only styles present in the current API catalogue.

    The model is asked to be conservative: if a style is not realistically achievable
    from the visible current hair/beard length, it must not recommend it.
    """
    if not GEMINI_API_KEY:
        raise HTTPException(503, 'Gemini is optional but required for image-based compatibility analysis. Add GEMINI_API_KEY to .env and restart GroomAI.')
    if kind not in ('hair', 'beard'):
        raise HTTPException(400, 'Style type must be hair or beard.')
    try:
        templates_list = json.loads(templates_json)
        preferences = json.loads(preferences_json or '{}')
    except json.JSONDecodeError:
        raise HTTPException(400, 'Invalid style or preference data.')
    if not isinstance(templates_list, list) or not templates_list:
        raise HTTPException(400, 'No real API templates are available. Connect YouCam and refresh the catalogue first.')
    raw = await file.read()
    if not raw:
        raise HTTPException(400, 'Upload a photo before asking for recommendations.')
    try:
        image = Image.open(BytesIO(raw)).convert('RGB')
        image.thumbnail((900, 900), Image.Resampling.LANCZOS)
        buff = BytesIO(); image.save(buff, format='JPEG', quality=82, optimize=True)
        image_b64 = __import__('base64').b64encode(buff.getvalue()).decode('ascii')
    except Exception as exc:
        raise HTTPException(400, f'Could not read image: {exc}')
    safe_templates = [{'id': str(t.get('id','')), 'name': str(t.get('name','')), 'group': str(t.get('group',''))} for t in templates_list if isinstance(t, dict)]
    prompt = f"""You are a cautious grooming-style compatibility assistant. Analyze the attached person's current visible {kind} and recommend only realistic styles from the exact allowed catalogue below. Do not recommend a transformation that requires substantially more hair length, a different hairline, or facial-hair density that is not visible. For hair, distinguish current length (very short/short/medium/long), visible texture if reasonably clear, and practical limitations. For beard, consider visible facial-hair coverage and density. Never infer ethnicity or make attractiveness judgments. If image quality is poor or the current style cannot be assessed, return few or zero recommendations and explain uncertainty. User preferences: {json.dumps(preferences)}. Allowed catalogue: {json.dumps(safe_templates)}. Return ONLY valid JSON with this schema: {{"current_style_summary":"short concise observation","limitations":["..."],"recommendations":[{{"id":"exact allowed id","reason":"why it is feasible from the current visible style","compatibility":"high|medium"}}]}}. Only use IDs from the allowed catalogue. Recommend at most 6 and do not invent styles."""
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}'
    body = {'contents':[{'parts':[{'text':prompt},{'inline_data':{'mime_type':'image/jpeg','data':image_b64}}]}], 'generationConfig':{'temperature':0.2, 'responseMimeType':'application/json'}}
    try:
        response = requests.post(url, json=body, timeout=45)
    except requests.RequestException as exc:
        raise HTTPException(502, f'Could not reach Gemini: {exc}')
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not response.ok:
        message = payload.get('error', {}).get('message', f'Gemini returned HTTP {response.status_code}')
        raise HTTPException(502, str(message))
    try:
        text = payload['candidates'][0]['content']['parts'][0]['text']
        parsed = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise HTTPException(502, 'Gemini returned an unreadable recommendation response. Please try again.')
    allowed = {t['id'] for t in safe_templates}
    parsed['recommendations'] = [r for r in parsed.get('recommendations', []) if isinstance(r, dict) and str(r.get('id')) in allowed][:6]
    return parsed


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
