# GroomAI barber booking and queue-management extension.
# This module is imported by main.py so the original YouCam studio remains available at /studio.
import base64 as _b64
import hashlib as _hashlib
import hmac as _hmac
import json as _json
import os as _os
import re as _re
import secrets as _secrets
import time as _time
import uuid as _uuid
from datetime import datetime as _datetime, timedelta as _timedelta
from pathlib import Path as _Path

from fastapi import HTTPException as _HTTPException, Request as _Request
from fastapi.responses import HTMLResponse as _HTMLResponse, RedirectResponse as _RedirectResponse, JSONResponse
from main import app, db, requests, sqlite3

_BOOKING_DIR = _Path(__file__).resolve().parent
_SESSION_SECRET = _os.getenv('GROOMAI_SESSION_SECRET', 'change-this-development-secret-before-deploying')
_SESSION_COOKIE = 'groomai_session'
_GOOGLE_CLIENT_ID = _os.getenv('GOOGLE_CLIENT_ID', '').strip()
_BARBER_USERNAME = _os.getenv('BARBER_USERNAME', 'barber@groomai.local').strip().lower()
_BARBER_PASSWORD = _os.getenv('BARBER_PASSWORD', 'Barber@12345')
_ADMIN_USERNAME = _os.getenv('ADMIN_USERNAME', 'admin@groomai.local').strip().lower()
_ADMIN_PASSWORD = _os.getenv('ADMIN_PASSWORD', 'GroomAI@12345')


def _now():
    return _datetime.now().astimezone()


def _iso(dt):
    return dt.isoformat(timespec='minutes') if dt else None


def _parse_dt(value):
    if not value:
        return None
    try:
        dt = _datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return dt
    except (ValueError, TypeError):
        return None


def _password_hash(password, salt=None):
    salt = salt or _secrets.token_bytes(16)
    digest = _hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 240_000)
    return _b64.urlsafe_b64encode(salt).decode().rstrip('=') + '$' + _b64.urlsafe_b64encode(digest).decode().rstrip('=')


def _verify_password(password, stored):
    try:
        salt_text, digest_text = stored.split('$', 1)
        salt = _b64.urlsafe_b64decode(salt_text + '=' * (-len(salt_text) % 4))
        actual = _hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 240_000)
        expected = _b64.urlsafe_b64decode(digest_text + '=' * (-len(digest_text) % 4))
        return _hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _make_session(user_id):
    payload = _b64.urlsafe_b64encode(_json.dumps({'uid': user_id, 'exp': int(_time.time()) + 60 * 60 * 24 * 14}).encode()).decode().rstrip('=')
    signature = _hmac.new(_SESSION_SECRET.encode(), payload.encode(), _hashlib.sha256).hexdigest()
    return payload + '.' + signature


def _read_session(token):
    try:
        payload, signature = token.split('.', 1)
        expected = _hmac.new(_SESSION_SECRET.encode(), payload.encode(), _hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(signature, expected):
            return None
        data = _json.loads(_b64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        if int(data.get('exp', 0)) < int(_time.time()):
            return None
        return str(data.get('uid', ''))
    except Exception:
        return None


def _public_user(row):
    if not row:
        return None
    return {'id': row['id'], 'username': row['username'], 'email': row['email'], 'phone': row['phone'],
            'avatar': row['avatar'] or '🙂', 'role': row['role'], 'auth_provider': row['auth_provider']}


def _current_user(request: _Request, roles=None, required=True):
    token = request.cookies.get(_SESSION_COOKIE, '')
    uid = _read_session(token) if token else None
    user = None
    if uid:
        with db() as con:
            user = con.execute('SELECT * FROM users WHERE id=? AND active=1', (uid,)).fetchone()
    if not user:
        if required:
            raise _HTTPException(401, 'Please sign in to continue.')
        return None
    if roles and user['role'] not in roles:
        raise _HTTPException(403, 'This account does not have permission to access this page.')
    return user


def _page(filename):
    return (_BOOKING_DIR / 'static' / filename).read_text(encoding='utf-8')


def _init_booking_db():
    with db() as con:
        # Earlier builds sometimes created these IDs as INTEGER PRIMARY KEY. This
        # version uses UUID text IDs, so rebuild those tables safely and copy rows.
        legacy_tables = {}
        for table in ('users', 'services', 'bookings'):
            table_exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if not table_exists:
                continue
            info = con.execute(f'PRAGMA table_info({table})').fetchall()
            id_column = next((row for row in info if row['name'] == 'id'), None)
            if id_column and str(id_column['type']).upper() == 'INTEGER' and int(id_column['pk'] or 0) == 1:
                legacy_name = f'{table}_legacy_text_id_migration'
                suffix = 1
                while con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (legacy_name,)).fetchone():
                    suffix += 1
                    legacy_name = f'{table}_legacy_text_id_migration_{suffix}'
                for idx in con.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,)).fetchall():
                    con.execute(f' DROP INDEX IF EXISTS "{idx[0]}"')
                con.execute(f'ALTER TABLE {table} RENAME TO {legacy_name}')
                legacy_tables[table] = legacy_name
        con.execute('''CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, email TEXT UNIQUE, phone TEXT DEFAULT '',
            avatar TEXT DEFAULT '🙂', password_hash TEXT, role TEXT NOT NULL DEFAULT 'customer',
            auth_provider TEXT NOT NULL DEFAULT 'password', active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        con.execute('''CREATE TABLE IF NOT EXISTS services (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '', price REAL NOT NULL,
            duration_minutes INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        con.execute('''CREATE TABLE IF NOT EXISTS bookings (
            id TEXT PRIMARY KEY, customer_id TEXT, customer_name TEXT NOT NULL, customer_phone TEXT DEFAULT '',
            service_ids TEXT NOT NULL, service_names TEXT NOT NULL, total_price REAL NOT NULL,
            duration_minutes INTEGER NOT NULL, queue_no INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'waiting',
            source TEXT NOT NULL DEFAULT 'online', estimated_start TEXT, arrival_by TEXT, started_at TEXT,
            completed_at TEXT, notes TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        # Upgrade databases from older GroomAI builds in place. CREATE TABLE IF NOT EXISTS
        # does not add columns to an existing table, which previously caused signup/login
        # to fail on users' existing groomai.db files.
        migrations = {
            'users': {
                'id': 'TEXT', 'username': "TEXT NOT NULL DEFAULT ''", 'email': 'TEXT',
                'phone': "TEXT DEFAULT ''", 'avatar': "TEXT DEFAULT '🙂'", 'password_hash': 'TEXT',
                'role': "TEXT NOT NULL DEFAULT 'customer'", 'auth_provider': "TEXT NOT NULL DEFAULT 'password'",
                'active': 'INTEGER NOT NULL DEFAULT 1', 'created_at': 'TEXT'
            },
            'services': {
                'id': 'TEXT', 'name': "TEXT NOT NULL DEFAULT 'Service'", 'description': "TEXT DEFAULT ''",
                'price': 'REAL NOT NULL DEFAULT 0', 'duration_minutes': 'INTEGER NOT NULL DEFAULT 15',
                'active': 'INTEGER NOT NULL DEFAULT 1', 'created_at': 'TEXT'
            },
            'bookings': {
                'id': 'TEXT', 'customer_id': 'TEXT', 'customer_name': "TEXT NOT NULL DEFAULT 'Customer'",
                'customer_phone': "TEXT DEFAULT ''", 'service_ids': "TEXT NOT NULL DEFAULT '[]'",
                'service_names': "TEXT NOT NULL DEFAULT '[]'", 'total_price': 'REAL NOT NULL DEFAULT 0',
                'duration_minutes': 'INTEGER NOT NULL DEFAULT 15', 'queue_no': 'INTEGER NOT NULL DEFAULT 0',
                'status': "TEXT NOT NULL DEFAULT 'waiting'", 'source': "TEXT NOT NULL DEFAULT 'online'",
                'estimated_start': 'TEXT', 'arrival_by': 'TEXT', 'started_at': 'TEXT', 'completed_at': 'TEXT',
                'notes': "TEXT DEFAULT ''", 'created_at': 'TEXT',
                'updated_at': 'TEXT'
            }
        }
        for table, columns in migrations.items():
            existing_columns = {row['name'] for row in con.execute(f'PRAGMA table_info({table})').fetchall()}
            for column, declaration in columns.items():
                if column not in existing_columns:
                    con.execute(f'ALTER TABLE {table} ADD COLUMN {column} {declaration}')
        # Copy rows from incompatible legacy tables into the new text-ID schema.
        copy_columns = {
            'users': ['id','username','email','phone','avatar','password_hash','role','auth_provider','active','created_at'],
            'services': ['id','name','description','price','duration_minutes','active','created_at'],
            'bookings': ['id','customer_id','customer_name','customer_phone','service_ids','service_names','total_price','duration_minutes','queue_no','status','source','estimated_start','arrival_by','started_at','completed_at','notes','created_at','updated_at'],
        }
        legacy_defaults = {
            'users': {'username': None, 'role': 'customer', 'auth_provider': 'password', 'active': 1, 'phone': '', 'avatar': '🙂'},
            'services': {'name': 'Legacy service', 'description': '', 'price': 0, 'duration_minutes': 15, 'active': 1},
            'bookings': {'customer_name': 'Legacy customer', 'customer_phone': '', 'service_ids': '[]', 'service_names': '[]', 'total_price': 0, 'duration_minutes': 15, 'queue_no': 0, 'status': 'waiting', 'source': 'online', 'notes': ''},
        }
        for table, legacy_name in legacy_tables.items():
            legacy_rows = con.execute(f'SELECT * FROM {legacy_name}').fetchall()
            target_cols = copy_columns[table]
            target_existing = {row['name'] for row in con.execute(f'PRAGMA table_info({table})').fetchall()}
            target_cols = [col for col in target_cols if col in target_existing]
            for old_row in legacy_rows:
                old = dict(old_row)
                row_cols, values = [], []
                for col in target_cols:
                    value = old.get(col)
                    if col == 'id':
                        value = str(value) if value not in (None, '') else str(_uuid.uuid4())
                    elif col == 'customer_id' and value is not None:
                        value = str(value)
                    elif col == 'username' and not value:
                        value = f'legacy_user_{_uuid.uuid4().hex[:8]}'
                    if value is None and col in legacy_defaults[table]:
                        value = legacy_defaults[table][col]
                    # Let SQLite apply defaults for nullable fields absent from old rows.
                    if value is None and col != 'id':
                        continue
                    row_cols.append(col)
                    values.append(value)
                placeholders = ','.join('?' for _ in row_cols)
                cols_sql = ','.join(row_cols)
                try:
                    con.execute(f'INSERT OR IGNORE INTO {table}({cols_sql}) VALUES({placeholders})', values)
                except sqlite3.IntegrityError:
                    pass
        # Give legacy rows stable IDs before normal application queries use them.
        for table in ('users', 'services', 'bookings'):
            rows = con.execute(f'SELECT rowid FROM {table} WHERE id IS NULL OR id = ? OR id = 0', ('',)).fetchall()
            for row in rows:
                con.execute(f'UPDATE {table} SET id=? WHERE rowid=?', (str(_uuid.uuid4()), row['rowid']))
        con.execute('CREATE INDEX IF NOT EXISTS idx_users_username_lower ON users(lower(username))')
        con.execute('CREATE INDEX IF NOT EXISTS idx_users_email_lower ON users(lower(email))')
        con.execute('CREATE INDEX IF NOT EXISTS idx_bookings_status_queue ON bookings(status, queue_no)')
        defaults = [
            ('Haircut', 'Classic or modern haircut', 200, 25),
            ('Haircut + Beard Trim', 'Haircut with beard shaping', 300, 40),
            ('Beard Trim', 'Trim and shape beard', 120, 15),
            ('Hair Wash', 'Shampoo and rinse', 80, 10),
            ('Hair Styling', 'Finishing and styling', 100, 15),
            ('Premium Grooming Package', 'Haircut, beard trim and hair wash', 420, 55),
        ]
        # Seed any missing defaults by name, even if an older database already has
        # one or two custom services saved.
        for name, description, price, duration in defaults:
            exists = con.execute('SELECT 1 FROM services WHERE lower(name)=lower(?) LIMIT 1', (name,)).fetchone()
            if not exists:
                con.execute('INSERT INTO services(id,name,description,price,duration_minutes) VALUES(?,?,?,?,?)',
                            (str(_uuid.uuid4()), name, description, price, duration))
        # Seed local demo accounts only when username does not already exist.
        for username, password, role, avatar in [
            (_BARBER_USERNAME, _BARBER_PASSWORD, 'barber', '💈'),
            (_ADMIN_USERNAME, _ADMIN_PASSWORD, 'admin', '🛡️'),
        ]:
            existing = con.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
            if not existing:
                con.execute('INSERT INTO users(id,username,email,phone,avatar,password_hash,role,auth_provider) VALUES(?,?,?,?,?,?,?,?)',
                            (str(_uuid.uuid4()), username, username, '', avatar, _password_hash(password), role, 'password'))
            else:
                # Treat staff credentials as environment-managed demo credentials.
                # Updating the same username in .env therefore updates its password on restart.
                con.execute('UPDATE users SET email=?, password_hash=?, role=?, avatar=?, active=1, auth_provider=? WHERE username=?',
                            (username, _password_hash(password), role, avatar, 'password', username))


_init_booking_db()


def _recompute_queue(con):
    """Re-estimate all active queue times from current service and waiting order."""
    now = _now()
    active = con.execute("SELECT * FROM bookings WHERE status IN ('in_service','waiting','booked') ORDER BY CASE status WHEN 'in_service' THEN 0 ELSE 1 END, queue_no ASC").fetchall()
    cursor = now
    current = next((row for row in active if row['status'] == 'in_service'), None)
    if current:
        started = _parse_dt(current['started_at']) or now
        cursor = max(now, started + _timedelta(minutes=int(current['duration_minutes'] or 0)))
        con.execute('UPDATE bookings SET estimated_start=?, arrival_by=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                    (_iso(started), _iso(now), current['id']))
    waiting = [row for row in active if row['status'] != 'in_service']
    for row in waiting:
        start = cursor
        arrival = max(now, start - _timedelta(minutes=10))
        con.execute('UPDATE bookings SET estimated_start=?, arrival_by=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                    (_iso(start), _iso(arrival), row['id']))
        cursor = start + _timedelta(minutes=int(row['duration_minutes'] or 0))


def _booking_dict(row, include_phone=True):
    d = dict(row)
    for key in ('service_ids', 'service_names'):
        try:
            d[key] = _json.loads(d[key] or '[]')
        except Exception:
            d[key] = []
    if not include_phone:
        d.pop('customer_phone', None)
    return d


def _next_queue_number(con):
    row = con.execute('SELECT COALESCE(MAX(queue_no),0)+1 FROM bookings').fetchone()
    return int(row[0])


def _validate_phone(phone):
    digits = _re.sub(r'\D', '', phone or '')
    if len(digits) < 10 or len(digits) > 15:
        raise _HTTPException(400, 'Enter a valid phone number with 10 to 15 digits.')
    return ('+' if str(phone).strip().startswith('+') else '') + digits


def _create_booking(con, customer_id, customer_name, phone, service_ids, source='online', notes=''):
    if not isinstance(service_ids, list) or not service_ids:
        raise _HTTPException(400, 'Choose at least one service.')
    unique_ids = list(dict.fromkeys(str(s) for s in service_ids))
    placeholders = ','.join('?' for _ in unique_ids)
    services = con.execute(f'SELECT * FROM services WHERE active=1 AND id IN ({placeholders})', unique_ids).fetchall()
    service_map = {s['id']: s for s in services}
    if len(service_map) != len(unique_ids):
        raise _HTTPException(400, 'One or more selected services are unavailable. Refresh the service list.')
    ordered = [service_map[sid] for sid in unique_ids]
    total_price = sum(float(s['price']) for s in ordered)
    duration = sum(int(s['duration_minutes']) for s in ordered)
    booking_id = str(_uuid.uuid4())
    queue_no = _next_queue_number(con)
    con.execute('''INSERT INTO bookings(id,customer_id,customer_name,customer_phone,service_ids,service_names,total_price,duration_minutes,queue_no,status,source,notes)
                   VALUES(?,?,?,?,?,?,?,?,?,'waiting',?,?)''',
                (booking_id, customer_id, customer_name, phone, _json.dumps(unique_ids), _json.dumps([s['name'] for s in ordered]), total_price, duration, queue_no, source, notes[:500]))
    _recompute_queue(con)
    return con.execute('SELECT * FROM bookings WHERE id=?', (booking_id,)).fetchone()


@app.get('/', response_class=_HTMLResponse)
def groomai_home():
    return _page('portal.html')

@app.get('/studio', response_class=_HTMLResponse)
def groomai_studio():
    return _page('index.html')

@app.get('/customer', response_class=_HTMLResponse)
def customer_frontend():
    return _page('customer.html')

@app.get('/barber', response_class=_HTMLResponse)
def barber_frontend():
    return _page('barber.html')

@app.get('/admin', response_class=_HTMLResponse)
def admin_frontend():
    return _page('admin.html')

@app.get('/customer/login', response_class=_HTMLResponse)
def customer_login_page():
    return _page('auth.html')

@app.get('/customer/signup', response_class=_HTMLResponse)
def customer_signup_page():
    return _page('auth.html')

@app.get('/barber/login', response_class=_HTMLResponse)
def barber_login_page():
    return _page('auth.html')

@app.get('/admin/login', response_class=_HTMLResponse)
def admin_login_page():
    return _page('auth.html')

@app.get('/api/config')
def booking_config():
    return {'google_client_id': _GOOGLE_CLIENT_ID, 'google_enabled': bool(_GOOGLE_CLIENT_ID)}

@app.post('/api/auth/signup')
def customer_signup(body: dict, response: _Request):
    username = str(body.get('username', '')).strip()
    email = str(body.get('email', '')).strip().lower() or None
    phone = _validate_phone(str(body.get('phone', '')))
    password = str(body.get('password', ''))
    avatar = str(body.get('avatar', '🙂'))[:12]
    if len(username) < 3 or len(username) > 32:
        raise _HTTPException(400, 'Username must be between 3 and 32 characters.')
    if not _re.fullmatch(r'[\w .@+-]+', username, flags=_re.UNICODE):
        raise _HTTPException(400, 'Username may contain letters, numbers, spaces, dots, @, +, _ or -.')
    if len(password) < 8:
        raise _HTTPException(400, 'Password must contain at least 8 characters.')
    if email and not _re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise _HTTPException(400, 'Enter a valid email address.')
    user_id = str(_uuid.uuid4())
    try:
        with db() as con:
            existing = con.execute('SELECT 1 FROM users WHERE lower(username)=lower(?) OR (? IS NOT NULL AND lower(email)=lower(?)) LIMIT 1',
                                   (username, email, email)).fetchone()
            if existing:
                raise _HTTPException(409, 'That username or email is already registered. Try signing in instead.')
            con.execute('INSERT INTO users(id,username,email,phone,avatar,password_hash,role,auth_provider) VALUES(?,?,?,?,?,?,?,?)',
                        (user_id, username, email, phone, avatar, _password_hash(password), 'customer', 'password'))
            user = con.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    except sqlite3.IntegrityError:
        raise _HTTPException(409, 'That username or email is already registered. Try signing in instead.')
    result = JSONResponse({'ok': True, 'user': _public_user(user)})
    result.set_cookie(_SESSION_COOKIE, _make_session(user_id), httponly=True, secure=_os.getenv('COOKIE_SECURE', '0') == '1', samesite='lax', max_age=60*60*24*14, path='/')
    return result

@app.post('/api/auth/login')
def user_login(body: dict):
    username = str(body.get('username', '')).strip().lower()
    password = str(body.get('password', ''))
    requested_role = str(body.get('role', 'customer')).strip().lower()
    if requested_role not in ('customer', 'barber', 'admin'):
        raise _HTTPException(400, 'Invalid login type.')
    with db() as con:
        user = con.execute('SELECT * FROM users WHERE lower(username)=? OR lower(email)=?', (username, username)).fetchone()
    if not user or not user['active'] or user['role'] != requested_role or not user['password_hash'] or not _verify_password(password, user['password_hash']):
        raise _HTTPException(401, 'Invalid username/password for this portal.')
    result = JSONResponse({'ok': True, 'user': _public_user(user)})
    result.set_cookie(_SESSION_COOKIE, _make_session(user['id']), httponly=True, secure=_os.getenv('COOKIE_SECURE', '0') == '1', samesite='lax', max_age=60*60*24*14, path='/')
    return result

@app.post('/api/auth/google')
def google_login(body: dict):
    if not _GOOGLE_CLIENT_ID:
        raise _HTTPException(503, 'Google sign-in is not configured yet. Set GOOGLE_CLIENT_ID in .env.')
    token = str(body.get('id_token', '')).strip()
    if not token:
        raise _HTTPException(400, 'Google did not return an ID token.')
    try:
        resp = requests.get('https://oauth2.googleapis.com/tokeninfo', params={'id_token': token}, timeout=10)
        claims = resp.json()
    except Exception:
        raise _HTTPException(502, 'Could not verify the Google sign-in token.')
    if not resp.ok or claims.get('aud') != _GOOGLE_CLIENT_ID or claims.get('iss') not in ('accounts.google.com', 'https://accounts.google.com') or claims.get('email_verified') not in (True, 'true', 'True'):
        raise _HTTPException(401, 'Google sign-in could not be verified. Please try again.')
    email = str(claims.get('email', '')).lower()
    display = str(claims.get('name') or email.split('@')[0])[:32]
    avatar = '🙂'
    user_id = None
    with db() as con:
        user = con.execute('SELECT * FROM users WHERE lower(email)=?', (email,)).fetchone()
        if user and user['role'] != 'customer':
            raise _HTTPException(403, 'Use the dedicated barber or admin login for this account.')
        if user:
            user_id = user['id']
            if not user['phone']:
                con.execute('UPDATE users SET auth_provider=? WHERE id=?', ('google', user_id))
        else:
            base = _re.sub(r'[^\w.-]', '', display.replace(' ', '.'))[:24] or 'customer'
            username = base
            suffix = 1
            while con.execute('SELECT 1 FROM users WHERE lower(username)=?', (username.lower(),)).fetchone():
                suffix += 1
                username = f'{base}{suffix}'[:32]
            user_id = str(_uuid.uuid4())
            con.execute('INSERT INTO users(id,username,email,phone,avatar,password_hash,role,auth_provider) VALUES(?,?,?,?,?,?,?,?)',
                        (user_id, username, email, '', avatar, None, 'customer', 'google'))
        user = con.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    result = JSONResponse({'ok': True, 'user': _public_user(user)})
    result.set_cookie(_SESSION_COOKIE, _make_session(user_id), httponly=True, secure=_os.getenv('COOKIE_SECURE', '0') == '1', samesite='lax', max_age=60*60*24*14, path='/')
    return result

@app.post('/api/auth/logout')
def user_logout():
    result = JSONResponse({'ok': True})
    result.delete_cookie(_SESSION_COOKIE, path='/')
    return result

@app.get('/api/me')
def get_me(request: _Request):
    user = _current_user(request, required=False)
    return {'user': _public_user(user)}

@app.patch('/api/profile')
def update_profile(body: dict, request: _Request):
    user = _current_user(request, roles=['customer'])
    phone = _validate_phone(str(body.get('phone', user['phone'] or '')))
    avatar = str(body.get('avatar', user['avatar'] or '🙂'))[:12]
    with db() as con:
        con.execute('UPDATE users SET phone=?, avatar=? WHERE id=?', (phone, avatar, user['id']))
        updated = con.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone()
    return {'ok': True, 'user': _public_user(updated)}

@app.get('/api/services')
def list_services():
    with db() as con:
        rows = con.execute('SELECT * FROM services WHERE active=1 ORDER BY name').fetchall()
    return {'items': [dict(r) for r in rows]}

@app.post('/api/bookings')
def create_customer_booking(body: dict, request: _Request):
    user = _current_user(request, roles=['customer'])
    phone = _validate_phone(str(body.get('phone') or user['phone'] or ''))
    with db() as con:
        con.execute('UPDATE users SET phone=? WHERE id=?', (phone, user['id']))
        row = _create_booking(con, user['id'], user['username'], phone, body.get('service_ids'), 'online', str(body.get('notes', '')))
        return {'ok': True, 'booking': _booking_dict(row, include_phone=False)}

@app.get('/api/bookings/mine')
def my_bookings(request: _Request):
    user = _current_user(request, roles=['customer'])
    with db() as con:
        rows = con.execute('SELECT * FROM bookings WHERE customer_id=? ORDER BY created_at DESC LIMIT 30', (user['id'],)).fetchall()
        _recompute_queue(con)
        rows = con.execute('SELECT * FROM bookings WHERE customer_id=? ORDER BY created_at DESC LIMIT 30', (user['id'],)).fetchall()
    return {'items': [_booking_dict(r, include_phone=False) for r in rows]}

@app.get('/api/queue')
def get_queue(request: _Request):
    _current_user(request, roles=['barber', 'admin'])
    with db() as con:
        _recompute_queue(con)
        rows = con.execute("SELECT * FROM bookings WHERE status IN ('waiting','booked','in_service') ORDER BY CASE status WHEN 'in_service' THEN 0 ELSE 1 END, queue_no ASC").fetchall()
        recent = con.execute("SELECT * FROM bookings WHERE status IN ('completed','skipped','no_show','cancelled') ORDER BY updated_at DESC LIMIT 15").fetchall()
    return {'items': [_booking_dict(r) for r in rows], 'recent': [_booking_dict(r) for r in recent]}

@app.post('/api/bookings/walkin')
def create_walkin(body: dict, request: _Request):
    _current_user(request, roles=['barber', 'admin'])
    name = str(body.get('customer_name', '')).strip()[:80]
    phone = _validate_phone(str(body.get('customer_phone', '')))
    if len(name) < 2:
        raise _HTTPException(400, 'Enter the walk-in customer name.')
    with db() as con:
        row = _create_booking(con, None, name, phone, body.get('service_ids'), 'walk-in', str(body.get('notes', '')))
        return {'ok': True, 'booking': _booking_dict(row)}

@app.patch('/api/bookings/{booking_id}/status')
def update_booking_status(booking_id: str, body: dict, request: _Request):
    _current_user(request, roles=['barber', 'admin'])
    new_status = str(body.get('status', '')).lower()
    allowed = {'waiting', 'in_service', 'completed', 'skipped', 'no_show', 'cancelled'}
    if new_status not in allowed:
        raise _HTTPException(400, 'Invalid queue status.')
    with db() as con:
        row = con.execute('SELECT * FROM bookings WHERE id=?', (booking_id,)).fetchone()
        if not row:
            raise _HTTPException(404, 'Booking not found.')
        if new_status == 'in_service' and row['status'] not in ('waiting', 'booked'):
            raise _HTTPException(409, 'Only a waiting booking can be started.')
        if new_status == 'completed' and row['status'] != 'in_service':
            raise _HTTPException(409, 'Only a service in progress can be completed.')
        if new_status in ('skipped', 'no_show', 'cancelled') and row['status'] not in ('waiting', 'booked'):
            raise _HTTPException(409, 'Only a waiting booking can be skipped or cancelled.')
        started_at = _iso(_now()) if new_status == 'in_service' else row['started_at']
        completed_at = _iso(_now()) if new_status == 'completed' else row['completed_at']
        queue_no = row['queue_no']
        if new_status == 'waiting' and row['status'] in ('skipped', 'no_show', 'cancelled'):
            queue_no = _next_queue_number(con)
        con.execute('UPDATE bookings SET status=?, started_at=?, completed_at=?, queue_no=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                    (new_status, started_at, completed_at, queue_no, booking_id))
        _recompute_queue(con)
        updated = con.execute('SELECT * FROM bookings WHERE id=?', (booking_id,)).fetchone()
    return {'ok': True, 'booking': _booking_dict(updated)}

@app.post('/api/services')
def add_service(body: dict, request: _Request):
    _current_user(request, roles=['admin'])
    name = str(body.get('name', '')).strip()[:80]
    description = str(body.get('description', '')).strip()[:250]
    try:
        price = float(body.get('price'))
        duration = int(body.get('duration_minutes'))
    except (TypeError, ValueError):
        raise _HTTPException(400, 'Enter a valid price and duration.')
    if not name or price < 0 or price > 100000 or duration < 5 or duration > 360:
        raise _HTTPException(400, 'Enter a service name, non-negative price, and duration between 5 and 360 minutes.')
    service_id = str(_uuid.uuid4())
    with db() as con:
        con.execute('INSERT INTO services(id,name,description,price,duration_minutes,active) VALUES(?,?,?,?,?,1)',
                    (service_id, name, description, price, duration))
        row = con.execute('SELECT * FROM services WHERE id=?', (service_id,)).fetchone()
    return {'ok': True, 'service': dict(row)}

@app.patch('/api/services/{service_id}')
def update_service(service_id: str, body: dict, request: _Request):
    _current_user(request, roles=['admin'])
    with db() as con:
        row = con.execute('SELECT * FROM services WHERE id=?', (service_id,)).fetchone()
        if not row:
            raise _HTTPException(404, 'Service not found.')
        name = str(body.get('name', row['name'])).strip()[:80]
        description = str(body.get('description', row['description'] or '')).strip()[:250]
        try:
            price = float(body.get('price', row['price']))
            duration = int(body.get('duration_minutes', row['duration_minutes']))
            active = 1 if body.get('active', bool(row['active'])) else 0
        except (TypeError, ValueError):
            raise _HTTPException(400, 'Enter a valid price and duration.')
        if not name or price < 0 or duration < 5 or duration > 360:
            raise _HTTPException(400, 'Check the service name, price, and duration.')
        con.execute('UPDATE services SET name=?,description=?,price=?,duration_minutes=?,active=? WHERE id=?',
                    (name, description, price, duration, active, service_id))
        updated = con.execute('SELECT * FROM services WHERE id=?', (service_id,)).fetchone()
    return {'ok': True, 'service': dict(updated)}

@app.get('/api/admin/summary')
def admin_summary(request: _Request):
    _current_user(request, roles=['admin'])
    with db() as con:
        today = _now().date().isoformat()
        total = con.execute("SELECT COUNT(*) FROM bookings WHERE substr(created_at,1,10)=?", (today,)).fetchone()[0]
        waiting = con.execute("SELECT COUNT(*) FROM bookings WHERE status IN ('waiting','booked')").fetchone()[0]
        in_service = con.execute("SELECT COUNT(*) FROM bookings WHERE status='in_service'").fetchone()[0]
        revenue = con.execute("SELECT COALESCE(SUM(total_price),0) FROM bookings WHERE status='completed' AND substr(updated_at,1,10)=?", (today,)).fetchone()[0]
        customers = con.execute("SELECT COUNT(*) FROM users WHERE role='customer' AND active=1").fetchone()[0]
        services = con.execute('SELECT * FROM services ORDER BY active DESC,name').fetchall()
    return {'today_bookings': total, 'waiting': waiting, 'in_service': in_service, 'today_revenue': revenue, 'customers': customers, 'services': [dict(r) for r in services]}

@app.get('/api/admin/customers')
def admin_customers(request: _Request):
    _current_user(request, roles=['admin'])
    with db() as con:
        rows = con.execute("SELECT id,username,email,phone,avatar,role,created_at FROM users WHERE role='customer' ORDER BY created_at DESC LIMIT 200").fetchall()
    return {'items': [dict(r) for r in rows]}
