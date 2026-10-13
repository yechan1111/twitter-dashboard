"""Local Signal dashboard. Python 3.10+, no third-party packages."""
import argparse
import getpass
import json
import os
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
import re
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('SIGNAL_DATA_DIR', str(ROOT / 'data')))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'dashboard.sqlite'
HANDLES = ['GGzy55', 'gat_only_you', 'seyyxz']
KST = timezone(timedelta(hours=9))
MONTH_LIMIT = 600
KEYCHAIN_SERVICE = 'SignalTwitterAPI'
QUERIES = {
    'crypto': '(비트코인 OR 이더리움 OR 에어드랍 OR 웹3) lang:ko -filter:retweets',
    'daily': '(일상 OR 셀카 OR 공부 OR 자기계발) lang:ko -filter:retweets',
}
REFRESH_LOCK = threading.Lock()
STOP = threading.Event()


def db():
    conn = sqlite3.connect(DB, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def initialize():
    with db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, updated REAL);
            CREATE TABLE IF NOT EXISTS requests (month TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY, handle TEXT, text TEXT, category TEXT, url TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS followers (handle TEXT, observed REAL, count INTEGER, PRIMARY KEY(handle, observed));
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS errors (key TEXT PRIMARY KEY, message TEXT);
        ''')
        # Existing cached measurements retain their original observation time.
        for row in conn.execute("SELECT * FROM cache WHERE key LIKE 'profile:%'"):
            profile = json.loads(row['value']).get('data') or {}
            count = profile.get('followers')
            if isinstance(count, int) and count >= 0:
                conn.execute('INSERT OR IGNORE INTO followers VALUES (?,?,?)',
                             (row['key'].split(':', 1)[1], row['updated'], count))


def api_key():
    key = os.environ.get('TWITTERAPI_IO_KEY')
    if key:
        return key
    if os.sys.platform == 'darwin':
        result = subprocess.run(['/usr/bin/security', 'find-generic-password', '-a',
                                 getpass.getuser(), '-s', KEYCHAIN_SERVICE, '-w'],
                                capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
    return None


def provider(path, handle, key, query=None):
    if not key:
        raise ValueError('API 키가 없습니다. 맥 설치 도구에서 키를 등록해주세요.')
    month = datetime.now(KST).strftime('%Y-%m')
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        count = conn.execute('SELECT count(*) FROM requests WHERE month=?', (month,)).fetchone()[0]
        if count >= MONTH_LIMIT:
            raise ValueError('월 API 요청 상한 도달 — 저장된 데이터를 표시합니다.')
        conn.execute('INSERT INTO requests VALUES (?,?)', (month, time.time()))
    params = {'query': query, 'queryType': 'Latest'} if query else {'userName': handle}
    req = urllib.request.Request('https://api.twitterapi.io' + path + '?' + urllib.parse.urlencode(params),
                                 headers={'X-API-Key': key})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise ValueError('데이터 제공사 오류 HTTP ' + str(error.code)) from None
    except Exception:
        raise ValueError('데이터 제공사 연결 실패') from None
    if not isinstance(result, dict) or result.get('status') == 'error':
        raise ValueError('데이터 제공사 응답 오류')
    return result


def slot(now=None):
    now = now or datetime.now(KST)
    candidates = [now.replace(hour=h, minute=0, second=0, microsecond=0) for h in (9, 15, 21)]
    candidates.append((now - timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0))
    return max(value for value in candidates if value <= now).isoformat()


def refresh(force=False):
    with REFRESH_LOCK:
        current_slot = slot()
        with db() as conn:
            previous = conn.execute("SELECT value FROM meta WHERE key='last_slot'").fetchone()
            if previous and previous['value'] == current_slot and not force:
                return False
            conn.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('last_slot', current_slot))
        key = api_key()
        for handle in HANDLES:
            for kind, path in [('profile', '/twitter/user/info'), ('tweets', '/twitter/user/last_tweets')]:
                cache_key = kind + ':' + handle
                if kind == 'profile':
                    with db() as conn:
                        previous_profile = conn.execute('SELECT updated FROM cache WHERE key=?', (cache_key,)).fetchone()
                    if previous_profile and datetime.fromtimestamp(previous_profile['updated'], KST).date() == datetime.now(KST).date():
                        continue
                try:
                    result = provider(path, handle, key)
                    if kind == 'profile':
                        profile = result.get('data') or {}
                        if not profile.get('id'):
                            raise ValueError('프로필 응답 형식을 확인해주세요.')
                    else:
                        posts = result.get('tweets', (result.get('data') or {}).get('tweets'))
                        if not isinstance(posts, list):
                            raise ValueError('최근 글 응답 형식을 확인해주세요.')
                    now = time.time()
                    with db() as conn:
                        conn.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)',
                                     (cache_key, json.dumps(result), now))
                        conn.execute('DELETE FROM errors WHERE key=?', (cache_key,))
                        count = (result.get('data') or {}).get('followers') if kind == 'profile' else None
                        if isinstance(count, int) and count >= 0:
                            conn.execute('INSERT OR IGNORE INTO followers VALUES (?,?,?)', (handle, now, count))
                except ValueError as error:
                    with db() as conn:
                        conn.execute('INSERT OR REPLACE INTO errors VALUES (?,?)', (cache_key, str(error)))
        for name, query in QUERIES.items():
            cache_key = 'search:' + name
            try:
                result = provider('/twitter/tweet/advanced_search', None, key, query=query)
                if not isinstance(result.get('tweets'), list):
                    raise ValueError('검색 응답 형식을 확인해주세요.')
                with db() as conn:
                    conn.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)',
                                 (cache_key, json.dumps(result), time.time()))
                    conn.execute('DELETE FROM errors WHERE key=?', (cache_key,))
            except ValueError as error:
                with db() as conn:
                    conn.execute('INSERT OR REPLACE INTO errors VALUES (?,?)', (cache_key, str(error)))
        return True


def observed_topics(conn):
    posts = {}
    times = []
    errors = [r['message'] for r in conn.execute("SELECT message FROM errors WHERE key LIKE 'search:%'")]
    for row in conn.execute("SELECT * FROM cache WHERE key LIKE 'search:%'"):
        times.append(row['updated'])
        for post in json.loads(row['value']).get('tweets', [])[:20]:
            identifier = str(post.get('id', ''))
            if not identifier.isdigit():
                continue
            author = post.get('author') or {}
            try:
                published = parsedate_to_datetime(post.get('createdAt', '')).timestamp()
            except (TypeError, ValueError):
                published = 0
            posts[identifier] = {'id': identifier, 'text': post.get('text', ''),
                                 'handle': author.get('userName', ''), 'name': author.get('name', ''),
                                 'url': 'https://x.com/i/status/' + identifier,
                                 'likes': post.get('likeCount', 0), 'replies': post.get('replyCount', 0),
                                 'reposts': post.get('retweetCount', 0), 'published': published}
    groups = [('비트코인', ['비트코인', 'btc']), ('이더리움', ['이더리움', 'eth']),
              ('에어드랍', ['에어드랍', 'airdrop']), ('Web3', ['웹3', 'web3']),
              ('공부', ['공부', '자기계발']), ('일상', ['일상']), ('셀카', ['셀카'])]
    hashtags = sorted({tag for p in posts.values() for tag in re.findall(r'#[가-힣A-Za-z0-9_]{2,30}', p['text'])})[:10]
    groups += [(tag, [tag.lower()]) for tag in hashtags]
    topics = []
    for label, words in groups:
        matches = [p for p in posts.values() if any(word in p['text'].lower() for word in words)]
        if matches:
            latest = max(matches, key=lambda p: p['published'])
            topics.append({'label': label, 'count': len(matches),
                           'authors': len({p['handle'] for p in matches if p['handle']}),
                           'latest': latest})
    return {'posts': sorted(posts.values(), key=lambda p: p['published'], reverse=True),
            'topics': sorted(topics, key=lambda t: t['count'], reverse=True)[:12],
            'updated': min(times) if times else None, 'error': ' / '.join(errors) or None,
            'queries': list(QUERIES.values()), 'sampleSize': len(posts)}


def scheduler():
    while not STOP.is_set():
        try:
            refresh()
        except Exception:
            print('예약 갱신 실패 — 다음 확인 때 재시도합니다.', flush=True)
        STOP.wait(30)


def snapshot():
    accounts = []
    with db() as conn:
        for handle in HANDLES:
            rows = {kind: conn.execute('SELECT * FROM cache WHERE key=?', (kind + ':' + handle,)).fetchone()
                    for kind in ('profile', 'tweets')}
            profile = json.loads(rows['profile']['value']).get('data') or {} if rows['profile'] else {}
            tweets = json.loads(rows['tweets']['value']) if rows['tweets'] else {}
            raw = tweets.get('tweets') or (tweets.get('data') or {}).get('tweets') or []
            posts = [{'id': post.get('id'), 'text': post.get('text', ''),
                      'url': 'https://x.com/' + handle + '/status/' + str(post.get('id', '')),
                      'likes': post.get('likeCount', 0), 'replies': post.get('replyCount', 0),
                      'reposts': post.get('retweetCount', 0), 'created': post.get('createdAt', '')}
                     for post in raw[:20]]
            errors = [r['message'] for r in conn.execute('SELECT message FROM errors WHERE key IN (?,?)',
                      ('profile:' + handle, 'tweets:' + handle))]
            history = [dict(r) for r in conn.execute(
                'SELECT observed,count FROM followers WHERE handle=? AND observed>? ORDER BY observed',
                (handle, time.time() - 30 * 86400))]
            accounts.append({'handle': handle, 'name': profile.get('name', handle),
                             'followers': profile.get('followers'), 'posts': posts,
                             'updated': rows['tweets']['updated'] if rows['tweets'] else None,
                             'profileUpdated': rows['profile']['updated'] if rows['profile'] else None,
                             'history': history, 'error': ' / '.join(errors) or None})
        records = [dict(r) for r in conn.execute('SELECT * FROM records ORDER BY created DESC LIMIT 100')]
        observation = observed_topics(conn)
        usage = conn.execute('SELECT count(*) FROM requests WHERE month=?',
                             (datetime.now(KST).strftime('%Y-%m'),)).fetchone()[0]
    return {'accounts': accounts, 'records': records, 'usage': usage, 'limit': MONTH_LIMIT,
            'observation': observation, 'schedule': ['09:00', '15:00', '21:00'], 'refreshing': REFRESH_LOCK.locked(), 'mode': 'local'}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def valid_host(self):
        return self.headers.get('Host') in ('127.0.0.1:' + str(self.server.server_port),
                                           'localhost:' + str(self.server.server_port))

    def send(self, status, data, content_type='application/json; charset=utf-8'):
        body = json.dumps(data, ensure_ascii=False).encode() if content_type.startswith('application/json') else data
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.valid_host():
            return self.send(403, {'error': 'Local access only'})
        if self.path == '/api/dashboard':
            try:
                self.send(200, snapshot())
            except Exception:
                self.send(500, {'error': '데이터를 불러오지 못했습니다.'})
        elif self.path == '/':
            self.send(200, (ROOT / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        else:
            self.send(404, {'error': 'Not found'})

    def do_POST(self):
        if not self.valid_host():
            return self.send(403, {'error': 'Local access only'})
        if self.path != '/api/records':
            return self.send(404, {'error': 'Not found'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.send(415, {'error': 'JSON required'})
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host', ''):
            return self.send(403, {'error': 'Origin rejected'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 10000:
                raise ValueError()
            value = json.loads(self.rfile.read(size))
            handle = value.get('handle')
            text = str(value.get('text', '')).strip()
            category = value.get('category')
            url = str(value.get('url', '')).strip()
            if handle not in HANDLES or not text or len(text) > 3000 or category not in ['크립토', '일상', '셀카', '공부'] or (url and not url.startswith('https://x.com/')):
                raise ValueError()
            with db() as conn:
                conn.execute('INSERT INTO records(handle,text,category,url,created) VALUES (?,?,?,?,?)',
                             (handle, text, category, url, time.time()))
            self.send(201, {'ok': True})
        except Exception:
            self.send(400, {'error': '계정, 내용, 카테고리와 X 링크를 확인해주세요.'})


initialize()
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--no-scheduler', action='store_true')
    parser.add_argument('--refresh-once', action='store_true')
    args = parser.parse_args()
    if args.refresh_once:
        refresh(force=True)
    else:
        if not args.no_scheduler:
            threading.Thread(target=scheduler, daemon=True).start()
        print('Dashboard listening on 127.0.0.1:' + str(args.port), flush=True)
        try:
            ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
        except KeyboardInterrupt:
            STOP.set()
