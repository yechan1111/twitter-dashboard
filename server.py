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
MONTH_LIMIT = 900
KEYCHAIN_SERVICE = 'SignalTwitterAPI'
CELEBRITY_TERMS = ['연예인', '연예계', '아이돌', '배우', '가수', '팬싸', '팬미팅', '팬덤',
                   '포토카드', '컴백', '콘서트', '예능', '드라마', '방탄소년단', '뉴진스',
                   '블랙핑크', '르세라핌', '에스파', '아이유', '임영웅', '차은우', '장원영']
EXCLUSIONS = ' '.join('-' + term for term in CELEBRITY_TERMS[:10] if term != '배우')
QUERIES = {
    'crypto': '(크립토 OR 코인 OR 솔라나 OR 이더리움 OR 에어드랍 OR 디파이 OR 웹3) lang:ko -filter:retweets ' + EXCLUSIONS,
    'daily': '(일상 OR 셀카 OR 운동 OR 커피 OR 하루 OR 루틴) lang:ko -filter:retweets ' + EXCLUSIONS,
    'ai': '(AI OR 인공지능 OR ChatGPT OR 클로드 OR 생성형) (정보 OR 방법 OR 팁 OR 정리 OR 활용 OR 도구) lang:ko -filter:retweets ' + EXCLUSIONS,
    'growth': '(크립토 OR 일상 OR 인공지능) lang:ko -filter:retweets ' + EXCLUSIONS,
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
            CREATE TABLE IF NOT EXISTS post_observations (post_id TEXT, observed REAL, followers INTEGER, views INTEGER, likes INTEGER, replies INTEGER, reposts INTEGER, PRIMARY KEY(post_id, observed));
            CREATE TABLE IF NOT EXISTS experiments (record_id INTEGER, window TEXT, views INTEGER, replies INTEGER, visits INTEGER, follows INTEGER, note TEXT, updated REAL, PRIMARY KEY(record_id, window));
        ''')
        for row in conn.execute("SELECT * FROM cache WHERE key LIKE 'search:%'"):
            if row['key'] in ['search:' + name for name in QUERIES]:
                for post in json.loads(row['value']).get('tweets', [])[:40]:
                    record_observation(conn, post, row['updated'])
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
    params = {'query': query, 'queryType': 'Top'} if query else {'userName': handle}
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
            if name == 'growth':
                with db() as conn:
                    previous = conn.execute('SELECT updated FROM cache WHERE key=?', (cache_key,)).fetchone()
                if previous and datetime.fromtimestamp(previous['updated'], KST).date() == datetime.now(KST).date():
                    continue
            try:
                since = (datetime.now(KST) - timedelta(days=7)).strftime('%Y-%m-%d')
                broad_query = query + ' since:' + since
                priority_query = broad_query + (' min_faves:3' if name == 'growth' else ' min_faves:10')
                result = provider('/twitter/tweet/advanced_search', None, key, query=priority_query)
                if not isinstance(result.get('tweets'), list):
                    raise ValueError('검색 응답 형식을 확인해주세요.')
                result['_queries'] = [priority_query]
                result['_mode'] = 'Top · 최근 7일 · 인기 후보 우선'
                # A popularity search is a discovery strategy, not the final filter.
                # If it finds no qualifying post, try a broader query without likes.
                if name != 'growth' and not any(qualifies(post) for post in result['tweets']):
                    try:
                        fallback = provider('/twitter/tweet/advanced_search', None, key, query=broad_query)
                        if not isinstance(fallback.get('tweets'), list):
                            raise ValueError('검색 응답 형식을 확인해주세요.')
                        result['tweets'] += fallback['tweets'][:20]
                        result['_queries'].append(broad_query)
                    except ValueError as error:
                        result['_fallback_error'] = str(error)
                observed = time.time()
                with db() as conn:
                    conn.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)',
                                 (cache_key, json.dumps(result), observed))
                    for post in result['tweets'][:40]:
                        record_observation(conn, post, observed)
                    conn.execute('DELETE FROM errors WHERE key=?', (cache_key,))
            except ValueError as error:
                with db() as conn:
                    conn.execute('INSERT OR REPLACE INTO errors VALUES (?,?)', (cache_key, str(error)))
        return True


def metric_count(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and re.fullmatch(r'[0-9]+', value.strip()):
        return int(value.strip())
    return None


def growth_insight(post):
    text = post.get('text', '')
    reasons = []
    if '?' in text or '？' in text or any(w in text for w in ['여러분', '다들', '어떻게']):
        reasons.append('질문으로 독자가 자기 경험을 답할 여지를 만듭니다.')
    if any(w in text for w in ['해봤', '느꼈', '배웠', '경험', '실패', '처음', '오늘']):
        reasons.append('경험과 변화가 드러나는 표현이 있어 개인 이야기 구조를 참고할 수 있습니다.')
    if any(w in text for w in ['정리', '방법', '팁', '이유', '배운', '공부', '체크']):
        reasons.append('설명·학습 구조를 참고해 독자가 얻을 내용을 분명히 할 수 있습니다.')
    if '\n' in text:
        reasons.append('줄바꿈으로 내용을 나눠 읽기 쉽게 구성했습니다.')
    if any(w in text for w in ['ㅋㅋ', '웃긴', '유머', '밈', '공감']):
        reasons.append('유머·공감 표현을 참고할 수 있습니다. 문맥 없이 그대로 따라 쓰지 마세요.')
    if not reasons:
        reasons.append('조회 기준은 통과했지만 텍스트에서 재사용할 구조적 단서는 적습니다.')
    if any(w in text for w in ['정리', '방법', '공부', '배운']):
        format_name, angle = '학습·설명형', '같은 주제를 직접 확인하고, 배운 점 하나와 아직 모르는 질문 하나를 자신의 말로 정리하세요.'
    elif any(w in text for w in ['ㅋㅋ', '웃긴', '유머', '밈']):
        format_name, angle = '유머·공감형 후보', '일상에서 실제로 겪은 작은 반전이나 공감 상황을 짧게 풀어보세요. 원문의 농담은 복사하지 마세요.'
    elif '?' in text or '？' in text:
        format_name, angle = '질문형', '내 실제 상황과 선택지 두 가지를 제시하고 경험을 묻는 질문으로 바꿔보세요.'
    elif any(w in text for w in ['오늘', '경험', '실패', '처음']):
        format_name, angle = '경험담 후보', '실제로 겪은 일 → 느낀 점 → 독자에게 묻는 질문 순서로 구성하세요.'
    else:
        format_name, angle = '짧은 의견형', '주제에 대한 내 관점과 그 이유를 더하세요. 원문의 문구나 경험을 그대로 가져오지 마세요.'
    category = classify_theme(text) or '일상'
    return {'category': category, 'format': format_name, 'reasons': reasons, 'angle': angle,
            'experiment': '게시 24시간 후 답글·프로필 방문·팔로우 결과를 기록하고 같은 포맷을 비교하세요.',
            'basis': '텍스트 규칙 분석 — 성장 효과·팔로우 전환은 검증되지 않았습니다.'}


def celebrity_related(text):
    return any(term in text for term in CELEBRITY_TERMS if term != '배우') or bool(re.search(r'배우(?:\s|가|의|를|님|들|로서|$)', text))


def classify_theme(text):
    lower = text.lower()
    if celebrity_related(text):
        return None
    if any(term in lower for term in ['크립토', '코인', '솔라나', '이더리움', '에어드랍', '디파이', '웹3', 'bitcoin', 'ethereum', 'web3', 'airdrop']):
        return '크립토'
    ai = re.search(r'(?<![a-z])ai(?![a-z])', lower) or any(term in lower for term in ['인공지능', 'chatgpt', '클로드', '생성형', '챗gpt'])
    information = any(term in lower for term in ['정보', '방법', '팁', '정리', '활용', '도구', '설명', '공부', '배운', '가이드', '기능'])
    if ai and information:
        return 'AI 정보'
    if any(term in text for term in ['일상', '셀카', '운동', '커피', '하루', '루틴', '오늘', '경험', '산책', '아침', '점심', '저녁']):
        return '일상'
    return None


def qualifies(post):
    followers = metric_count((post.get('author') or {}).get('followers'))
    views = metric_count(post.get('viewCount'))
    author = post.get('author') or {}
    celebrity = celebrity_related(post.get('text', '') + ' ' + str(author.get('name', '')) + ' ' + str(author.get('description', '')))
    return not celebrity and classify_theme(post.get('text', '')) is not None and followers is not None and views is not None and followers >= 1000 and views >= 3000


def post_structure(raw):
    text = raw.get('text', '').strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first_line = lines[0] if lines else ''
    sentence = re.split(r'(?<=[.!?。！？])\s+', first_line, maxsplit=1)[0]
    hooks = []
    evidence = []
    if '?' in sentence or '？' in sentence or any(term in sentence for term in ['어떻게', '왜 ', '여러분', '다들']):
        hooks.append('질문형 후보')
        evidence.append('첫 문장의 질문 표현을 감지했습니다.')
    if any(term in sentence for term in ['나만', '공감', '다들', '우리', '오늘', 'ㅋㅋ']):
        hooks.append('공감형 후보')
        evidence.append('첫 문장에 일상·독자 호명·공감 표현이 있습니다.')
    if any(term in sentence for term in ['그런데', '근데', '반전', '의외', '알고 보니', '사실은']):
        hooks.append('반전형 후보')
        evidence.append('첫 문장의 대비·반전 표현을 감지했습니다.')
    if not hooks:
        hooks.append('직접 진술형 후보')
        evidence.append('질문·공감·반전 규칙에 일치하지 않는 첫 문장입니다.')
    media = (raw.get('extendedEntities') or {}).get('media') or []
    if any(m.get('type') in ('video','animated_gif') for m in media):
        content_format = '영상·GIF + 문구'
        format_basis = '제공사 응답에 영상 또는 GIF 미디어가 있습니다.'
    elif any(m.get('type') == 'photo' for m in media):
        content_format = '사진 + 문구'
        format_basis = '제공사 응답에 사진 미디어가 있습니다.'
    elif any(term in text for term in ['정리', '방법', '팁', '가이드']):
        content_format = '정보 요약 후보'
        format_basis = '본문의 정리·방법·팁 표현을 감지했습니다.'
    elif len(lines) >= 3:
        content_format = '문단형 텍스트'
        format_basis = '비어 있지 않은 줄이 3개 이상입니다. 스레드 여부는 확인하지 않았습니다.'
    else:
        content_format = '짧은 텍스트'
        format_basis = '텍스트 구조로 분류했습니다. 미디어 누락 가능성은 있습니다.'
    return {'firstSentence': sentence, 'hooks': hooks, 'hookEvidence': evidence,
            'format': content_format, 'formatBasis': format_basis,
            'classificationBasis': '규칙 기반 후보 — 사람이 문맥을 확인해야 합니다.'}


def record_observation(conn, raw, observed):
    identifier = str(raw.get('id', ''))
    author = raw.get('author') or {}
    if not identifier.isdigit() or not classify_theme(raw.get('text', '')) or celebrity_related(str(author.get('name', '')) + ' ' + str(author.get('description', ''))):
        return
    conn.execute('INSERT OR IGNORE INTO post_observations VALUES (?,?,?,?,?,?,?)',
                 (identifier, observed, metric_count(author.get('followers')), metric_count(raw.get('viewCount')),
                  metric_count(raw.get('likeCount')), metric_count(raw.get('replyCount')), metric_count(raw.get('retweetCount'))))


def observed_topics(conn):
    candidates = {}
    times = []
    queries = []
    modes = []
    errors = [r['message'] for r in conn.execute("SELECT key,message FROM errors WHERE key LIKE 'search:%'") if r['key'] in ['search:' + name for name in QUERIES]]
    for row in conn.execute("SELECT * FROM cache WHERE key LIKE 'search:%'"):
        if row['key'] not in ['search:' + name for name in QUERIES]:
            continue
        times.append(row['updated'])
        cached_search = json.loads(row['value'])
        queries.extend(cached_search.get('_queries', []))
        modes.append(cached_search.get('_mode', 'Latest · 이전 검색 캐시'))
        if cached_search.get('_fallback_error'):
            errors.append(cached_search['_fallback_error'])
        for post in cached_search.get('tweets', [])[:40]:
            identifier = str(post.get('id', ''))
            if not identifier.isdigit():
                continue
            if identifier in candidates and candidates[identifier]['collectedAt'] >= row['updated']:
                continue
            author = post.get('author') or {}
            try:
                published = parsedate_to_datetime(post.get('createdAt', '')).timestamp()
            except (TypeError, ValueError):
                published = 0
            candidates[identifier] = {'id': identifier, 'text': post.get('text', ''),
                                 'collectedAt': row['updated'], 'structure': post_structure(post),
                                 'observations': [dict(r) for r in conn.execute('SELECT observed,followers,views,likes,replies,reposts FROM post_observations WHERE post_id=? ORDER BY observed DESC LIMIT 10', (identifier,))][::-1],
                                 'celebrityRelated': celebrity_related(post.get('text', '') + ' ' + str(author.get('name', '')) + ' ' + str(author.get('description', ''))),
                                 'followers': metric_count(author.get('followers')),
                                 'views': metric_count(post.get('viewCount')),
                                 'handle': author.get('userName', ''), 'name': author.get('name', ''),
                                 'url': 'https://x.com/i/status/' + identifier,
                                 'likes': metric_count(post.get('likeCount')), 'replies': metric_count(post.get('replyCount')),
                                 'reposts': metric_count(post.get('retweetCount')), 'published': published}
    posts = {}
    missing = 0
    below = 0
    off_topic = 0
    growth_cases = []
    audience = []
    for identifier, post in candidates.items():
        theme = classify_theme(post['text'])
        if not theme or post['celebrityRelated']:
            off_topic += 1
            continue
        post['theme'] = theme
        if post['followers'] is not None and post['views'] is not None and 100 <= post['followers'] < 1000 and post['views'] >= 300:
            post['growth'] = growth_insight(post)
            growth_cases.append(post)
        if ('?' in post['text'] or '？' in post['text']) and post['published']:
            audience.append(post)
        if post['followers'] is None or post['views'] is None:
            missing += 1
        elif post['followers'] < 1000 or post['views'] < 3000:
            below += 1
        else:
            posts[identifier] = post
    for post in posts.values():
        post['growth'] = growth_insight(post)
        post['strongReach'] = post['views'] >= 5000
    groups = [(theme, []) for theme in ['크립토', '일상', 'AI 정보']]
    topics = []
    for label, words in groups:
        matches = [p for p in posts.values() if p['theme'] == label]
        if matches:
            latest = max(matches, key=lambda p: p['published'])
            topics.append({'label': label, 'count': len(matches),
                           'authors': len({p['handle'] for p in matches if p['handle']}),
                           'latest': latest})
    return {'posts': sorted(posts.values(), key=lambda p: p['published'], reverse=True),
            'topics': sorted(topics, key=lambda t: t['count'], reverse=True)[:12],
            'updated': min(times) if times else None, 'error': ' / '.join(errors) or None,
            'queries': queries or list(QUERIES.values()), 'searchMode': ' / '.join(dict.fromkeys(modes)), 'sampleSize': len(posts),
            'scannedSize': len(candidates), 'rejectedBelow': below, 'rejectedMissing': missing,
            'rejectedTheme': off_topic, 'growthCases': sorted(growth_cases, key=lambda p:p['published'], reverse=True)[:12],
            'audienceQuestions': sorted(audience, key=lambda p:p['published'], reverse=True)[:12],
            'filters': {'minFollowers': 1000, 'minViews': 3000, 'strongViews': 5000}}


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
        experiments = [dict(r) for r in conn.execute('SELECT * FROM experiments ORDER BY updated DESC')]
        for record in records:
            record['experiments'] = [e for e in experiments if e['record_id'] == record['id']]
        observation = observed_topics(conn)
        usage = conn.execute('SELECT count(*) FROM requests WHERE month=?',
                             (datetime.now(KST).strftime('%Y-%m'),)).fetchone()[0]
    return {'accounts': accounts, 'records': records, 'usage': usage, 'limit': MONTH_LIMIT,
            'observation': observation, 'schedule': ['09:00', '15:00', '21:00'], 'refreshing': REFRESH_LOCK.locked(), 'mode': 'local'}


def save_experiment(value):
    record_id = value.get('record_id')
    window = value.get('window')
    if not isinstance(record_id, int) or window not in ('24h', '72h'):
        raise ValueError('기록과 관측 시점을 확인해주세요.')
    metrics = []
    for field in ['views', 'replies', 'visits', 'follows']:
        raw = value.get(field)
        parsed = metric_count(raw) if raw is not None and raw != '' else None
        if raw is not None and raw != '' and (parsed is None or parsed > 1000000000):
            raise ValueError('지표는 0 이상의 정수 또는 빈칸이어야 합니다.')
        metrics.append(parsed)
    if all(v is None for v in metrics):
        raise ValueError('한 개 이상의 관측 지표가 필요합니다.')
    note = str(value.get('note', '')).strip()
    if len(note) > 1000:
        raise ValueError('메모가 너무 깁니다.')
    with db() as conn:
        if not conn.execute('SELECT id FROM records WHERE id=?', (record_id,)).fetchone():
            raise ValueError('게시 기록이 없습니다.')
        conn.execute('INSERT OR REPLACE INTO experiments VALUES (?,?,?,?,?,?,?,?)',
                     (record_id, window, *metrics, note, time.time()))


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
        if self.path not in ('/api/records', '/api/experiments'):
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
            if self.path == '/api/experiments':
                save_experiment(value)
                return self.send(201, {'ok': True})
            handle = value.get('handle')
            text = str(value.get('text', '')).strip()
            category = value.get('category')
            url = str(value.get('url', '')).strip()
            if handle not in HANDLES or not text or len(text) > 3000 or category not in ['크립토', '일상', 'AI 정보', '셀카', '공부'] or (url and not url.startswith('https://x.com/')):
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
