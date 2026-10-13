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
FANDOM_TERMS = ['팬스 초이스', '팬스초이스', 'fans choice', 'fan choice', '팬 투표', '팬투표', '팬덤 투표', '투표 독려', '음방', '음악방송', '스밍', '뮤직비디오', '아이들', '여자아이들']
EXCLUSIONS = ' '.join('-' + term for term in CELEBRITY_TERMS[:10] if term != '배우') + ' -MAMAVOTE -팬투표 -팬스초이스'
QUERIES = {
    'crypto': '(크립토 OR 코인 OR 솔라나 OR 이더리움 OR 에어드랍 OR 디파이 OR 웹3 OR 금리 OR 환율 OR FOMC OR CPI OR 경제시황 OR 규제 OR 해킹 OR 온체인 OR 블록체인 OR 거래소 OR 디파이) lang:ko -filter:retweets ' + EXCLUSIONS,
    'daily': '(일상 OR 셀카 OR 운동 OR 커피 OR 하루 OR 루틴) lang:ko -filter:retweets ' + EXCLUSIONS,
    'ai': '(AI OR 인공지능 OR ChatGPT OR 클로드 OR 생성형) (영상 OR 릴스 OR 쇼츠 OR 블로그 OR 인스타 OR 콘텐츠 OR 부업 OR 카드뉴스 OR 작업대행) lang:ko -filter:retweets ' + EXCLUSIONS,
    'growth': '(크립토 OR 일상 OR 인공지능) lang:ko -filter:retweets ' + EXCLUSIONS,
}
PERSONAS = {
    'GGzy55': {'name': 'gg', 'level': '크립토 초보',
        'focus': ['크립토 입문', '일상', '셀카', '밋업'],
        'keywords': ['셀카', '일상', '밋업', 'meetup', '커피', '산책', '입문', '초보', '릴스', '블로그', '인스타'],
        'themeWeights': {'일상': 3, '크립토': 2, 'AI 정보': 1},
        'angle': '초보자의 질문이나 실제 일상·사진·밋업 경험으로 풀어보세요.',
        'templates': [
            {'cat':'크립토','title':'초보자의 질문 하나로 대화 열기','text':'오늘 [직접 읽은 크립토 소식]에서 궁금해진 점: [구체적인 질문].\n내가 이해한 건 [확인한 내용]인데, 이 부분은 더 공부해봐야겠다.'},
            {'cat':'일상','title':'실제 사진과 하루의 맥락','text':'[직접 찍은 사진] + 오늘 [실제로 있었던 일].\n[짧은 감상] — 여러분의 하루는 어땠나요?'},
            {'cat':'일상','title':'밋업에서 기억에 남은 순간','text':'[실제로 참여한 밋업]에서 기억에 남은 건 [장면이나 대화].\n처음 알게 된 점은 [확인한 사실]. 다음엔 [질문]도 물어보고 싶다.'}]},
    'gat_only_you': {'name':'유경','level':'크립토 초보',
        'focus':['크립토 전반 이슈','프로젝트·생태계','뉴스·시장 상황','규제·보안','에어드랍 작업','밋업'],
        'keywords':['에어드랍','airdrop','작업','참여','조건','뉴스','시장','프로젝트','생태계','규제','보안','해킹','온체인','블록체인','거래소','밋업','meetup','조사','정리','콘텐츠'],
        'themeWeights':{'크립토':3,'AI 정보':1,'일상':1},
        'angle':'크립토 전반에서 무슨 일이 일어났고 왜 중요한지 초보자 눈높이로 정리하세요. 프로젝트·생태계·규제·보안, 에어드랍, 밋업 이슈를 원출처로 확인하고 매매 판단과 구분하세요.',
        'templates':[
            {'cat':'크립토','title':'에어드랍 작업 전 체크할 것','text':'[공식 출처로 확인한 프로젝트]를 살펴보는 중.\n확인한 참여 조건은 [조건], 아직 확인할 건 [질문].\n보상은 확정된 게 아니니 공지와 지갑 권한부터 체크하려고 한다.'},
            {'cat':'크립토','title':'크립토 이슈, 무슨 일이고 왜 중요할까','text':'[원출처와 발표 시각이 있는 크립토 이슈]: [확인한 사실].\n중요해 보이는 이유는 [근거], 아직 확인할 건 [질문].\n초보 입장에서 이렇게 이해했는데 추가로 살펴보려고 한다.'},
            {'cat':'일상','title':'밋업 공부 메모','text':'[실제로 참여한 밋업]에서 배운 개념 하나: [직접 작성].\n아직 헷갈리는 건 [질문]. 다음에 직접 확인해보고 싶다.'}]},
    'seyyxz': {'name':'세인','level':'크립토 초보 · 실제 트레이딩 운영',
        'focus':['실제 트레이딩','시장 현황','경제 시황','뉴스','일상','밋업'],
        'keywords':['트레이딩','매매','차트','리스크','시장','뉴스','금리','환율','fomc','cpi','경제','일상','밋업','meetup','시황','매매일지','복기'],
        'themeWeights':{'크립토':3,'일상':2,'AI 정보':1},
        'angle':'실제 매매의 판단·리스크 관리, 시장 현황과 경제 시황, 일상으로 연결하세요. 확인한 사실과 개인 전망을 구분하고 수익·거래를 만들어내지 마세요.',
        'templates':[
            {'cat':'크립토','title':'실제 트레이딩의 판단과 복기','text':'오늘 [실제로 실행한 매매]에서 본 근거는 [직접 작성].\n리스크 기준은 [실제 기준], 복기할 점은 [배운 점].\n수익이나 체결 결과는 확인한 값만 기록한다.'},
            {'cat':'크립토','title':'시장·경제 시황, 사실과 관점 나누기','text':'[발표 시각과 출처가 있는 경제 지표/뉴스]: [확인한 수치].\n현재 시장에서 관찰한 것은 [실제 관측].\n내 관점은 [판단과 근거], 달라질 수 있는 조건은 [변수].'},
            {'cat':'일상','title':'공부와 일상 사이의 한 장면','text':'오늘 [실제로 있었던 일] 덕분에 [느낀 점].\n공부하다가 이런 순간도 필요한 것 같다. 여러분은 어떻게 쉬나요?'}]},
}


AI_CONTENT_TERMS = ['영상', '릴스', '쇼츠', '블로그', '인스타', '콘텐츠', '부업', '카드뉴스', '작업대행', '유튜브']
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
            CREATE TABLE IF NOT EXISTS materials (id INTEGER PRIMARY KEY, source_id TEXT, handle TEXT, source TEXT, category TEXT, angle TEXT, draft TEXT, resources TEXT, status TEXT, publish_at TEXT, post_url TEXT, record_id INTEGER, updated REAL, UNIQUE(source_id, handle));
            CREATE TABLE IF NOT EXISTS post_archive (post_id TEXT PRIMARY KEY, raw TEXT, first_seen REAL, last_seen REAL);
            CREATE TABLE IF NOT EXISTS post_observations (post_id TEXT, observed REAL, followers INTEGER, views INTEGER, likes INTEGER, replies INTEGER, reposts INTEGER, PRIMARY KEY(post_id, observed));
            CREATE TABLE IF NOT EXISTS experiments (record_id INTEGER, window TEXT, views INTEGER, replies INTEGER, visits INTEGER, follows INTEGER, note TEXT, updated REAL, PRIMARY KEY(record_id, window));
        ''')
        for row in conn.execute("SELECT * FROM cache WHERE key LIKE 'search:%' OR key LIKE 'tweets:%'"):
            if row['key'] in ['search:' + name for name in QUERIES] + ['tweets:' + handle for handle in HANDLES]:
                cached = json.loads(row['value'])
                for post in (cached.get('tweets') or (cached.get('data') or {}).get('tweets') or [])[:40]:
                    if row['key'].startswith('tweets:') and not post.get('author'):
                        post = {**post, 'author': {'userName': row['key'].split(':',1)[1]}}
                    record_observation(conn, post, row['updated'])
        for row in conn.execute('SELECT source,updated FROM materials'):
            source = json.loads(row['source'])
            if classify_theme(source.get('text', '')) and not source.get('celebrityRelated'):
                archive_raw(conn, source['id'], json.dumps({'normalized': source}), source.get('collectedAt') or row['updated'])
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
                        if kind == 'tweets':
                            for post in posts[:20]:
                                record_observation(conn,{**post,'author':post.get('author') or {'userName':handle}},now)
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


def practical_insight(text):
    evidence = []
    if any(term in text for term in ['직접', '해봤', '만들었', '테스트', '실험', '작업물', '결과물']):
        evidence.append('직접 사용·제작 또는 작업물에 대한 언급')
    if re.search(r'(?:^|\n)\s*[1-9][.)]|[1-9]단계', text) or any(term in text for term in ['단계', '순서', '과정', '방법']):
        evidence.append('따라 해볼 단계·과정 설명')
    if any(term in text for term in ['시간', '비용', '요금', '분 걸', '원 들']):
        evidence.append('소요 시간·비용 언급')
    if any(term in text for term in ['한계', '실패', '어려', '단점', '주의']):
        evidence.append('한계·실패·주의점 언급')
    flags = []
    if any(term in text for term in ['수익 보장', '수익보장', '무조건 돈', '월 천', '월천', '누구나 돈', '100% 수익']):
        flags.append('과장·수익 보장 표현 검토 필요')
    if any(term in text for term in ['강의', '수강', '제휴', '추천코드', '할인코드', '쿠폰']):
        flags.append('강의 판매·제휴 홍보 가능성 검토')
    return {'evidence': evidence, 'flags': flags,
            'basis': '본문의 표현 단서이며 작업물·수익·광고 관계를 확인한 결과는 아닙니다.'}


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
            'basis': '텍스트 규칙 분석 — 성장 효과·팔로우 전환은 검증되지 않았습니다.',
            'practical': practical_insight(text)}


def celebrity_related(text):
    lower = text.lower()
    if re.search(r'#(?:\d{4})?mamavote\b|mamavote|#(?:idle|gidle|g_idle|bts|blackpink|newjeans)\b', lower):
        return True
    # '아이들' alone also means children; require idol/fan context for that term.
    if any(term in lower for term in FANDOM_TERMS if term not in ('아이들',)):
        return True
    if '아이들' in text and any(term in lower for term in ['투표', '팬', '컴백', 'rt', '해시태그']):
        return True
    if re.search(r'\bmama\b', lower) and any(term in lower for term in ['투표', 'vote', '팬', '시상식', '초이스']):
        return True
    return any(term in text for term in CELEBRITY_TERMS if term != '배우') or bool(re.search(r'배우(?:\s|가|의|를|님|들|로서|$)', text))


def classify_theme(text):
    lower = text.lower()
    if celebrity_related(text):
        return None
    if any(term in lower for term in ['크립토', '코인', '솔라나', '이더리움', '에어드랍', '디파이', '웹3', 'bitcoin', 'ethereum', 'web3', 'airdrop', '금리', '환율', 'fomc', 'cpi', '경제시황', '경제 시황', '블록체인', '온체인', '디파이']):
        return '크립토'
    ai = re.search(r'(?<![a-z])ai(?![a-z])', lower) or any(term in lower for term in ['인공지능', 'chatgpt', '클로드', '생성형', '챗gpt'])
    information = any(term in lower for term in ['정보', '방법', '팁', '정리', '활용', '도구', '설명', '공부', '배운', '가이드', '기능'])
    if ai and any(term in text for term in AI_CONTENT_TERMS):
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


def archive_raw(conn, identifier, raw, observed):
    conn.execute('INSERT INTO post_archive VALUES (?,?,?,?) ON CONFLICT(post_id) DO UPDATE SET raw=CASE WHEN excluded.last_seen>=post_archive.last_seen THEN excluded.raw ELSE post_archive.raw END, first_seen=MIN(post_archive.first_seen,excluded.first_seen), last_seen=MAX(post_archive.last_seen,excluded.last_seen)',
                 (identifier, raw, observed, observed))


def archived_post(conn, row):
    payload = json.loads(row['raw'])
    if 'normalized' in payload:
        post = payload['normalized']
    else:
        raw = payload['tweet']
        author = raw.get('author') or {}
        try:
            published = parsedate_to_datetime(raw.get('createdAt', '')).timestamp()
        except (TypeError, ValueError):
            published = 0
        post = {'id':row['post_id'],'text':raw.get('text',''),'handle':author.get('userName',''),
                'name':author.get('name',''),'url':'https://x.com/i/status/'+row['post_id'],
                'followers':metric_count(author.get('followers')),'views':metric_count(raw.get('viewCount')),
                'likes':metric_count(raw.get('likeCount')),'replies':metric_count(raw.get('replyCount')),
                'reposts':metric_count(raw.get('retweetCount')),'published':published,
                'celebrityRelated':celebrity_related(raw.get('text','')+' '+str(author.get('name',''))+' '+str(author.get('description',''))),
                'structure':post_structure(raw)}
    theme = classify_theme(post['text'])
    if not theme or post.get('celebrityRelated'):
        return None
    return {**post,'theme':theme,'collectedAt':row['last_seen'],'firstSeen':row['first_seen'],
            'growth':growth_insight(post),
            'observations':[dict(r) for r in conn.execute('SELECT observed,followers,views,likes,replies,reposts FROM post_observations WHERE post_id=? ORDER BY observed DESC LIMIT 10',(row['post_id'],))][::-1]}


def archive_page(query='', theme='', page=1):
    if len(query)>200 or theme not in ('','크립토','일상','AI 정보') or page<1 or page>100000:
        raise ValueError('검색 조건을 확인해주세요.')
    posts=[]
    with db() as conn:
        for row in conn.execute('SELECT * FROM post_archive ORDER BY last_seen DESC,post_id DESC'):
            post=archived_post(conn,row)
            if post and (not theme or post['theme']==theme) and (not query or query.lower() in (post['text']+' '+post.get('handle','')).lower()):
                posts.append(post)
    return {'posts':posts[(page-1)*20:page*20],'total':len(posts),'page':page,'pageSize':20}


def record_observation(conn, raw, observed):
    identifier = str(raw.get('id', ''))
    author = raw.get('author') or {}
    if not identifier.isdigit() or not classify_theme(raw.get('text', '')) or celebrity_related(str(author.get('name', '')) + ' ' + str(author.get('description', ''))):
        return
    archive_raw(conn, identifier, json.dumps({'tweet': raw}), observed)
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
                             'persona': PERSONAS[handle], 'updated': rows['tweets']['updated'] if rows['tweets'] else None,
                             'profileUpdated': rows['profile']['updated'] if rows['profile'] else None,
                             'history': history, 'error': ' / '.join(errors) or None})
        records = [dict(r) for r in conn.execute('SELECT * FROM records ORDER BY created DESC LIMIT 100')]
        experiments = [dict(r) for r in conn.execute('SELECT * FROM experiments ORDER BY updated DESC')]
        for record in records:
            record['experiments'] = [e for e in experiments if e['record_id'] == record['id']]
        observation = observed_topics(conn)
        for account in accounts:
            account['recommendations'] = recommend_posts(account['handle'], observation['posts'])
        materials = [dict(r) for r in conn.execute('SELECT * FROM materials ORDER BY updated DESC')]
        for material in materials:
            material['source'] = json.loads(material['source'])
        usage = conn.execute('SELECT count(*) FROM requests WHERE month=?',
                             (datetime.now(KST).strftime('%Y-%m'),)).fetchone()[0]
    return {'accounts': accounts, 'records': records, 'usage': usage, 'limit': MONTH_LIMIT,
            'observation': observation, 'materials': materials, 'schedule': ['09:00', '15:00', '21:00'], 'refreshing': REFRESH_LOCK.locked(), 'mode': 'local'}


def recommend_posts(handle, posts):
    persona = PERSONAS[handle]
    recommendations = []
    for post in posts:
        text = post['text'].lower()
        words = [word for word in persona['keywords'] if word in text]
        practical = practical_insight(post['text'])
        score = persona['themeWeights'].get(post['theme'],0) + min(len(words),3) * 2
        if post['theme'] == 'AI 정보':
            score += len(practical['evidence']) - 3 * len(practical['flags'])
        reasons = ['관심 테마: ' + post['theme']]
        if words:
            reasons.append('관심 표현: ' + ', '.join(words[:3]))
        else:
            reasons.append('직접 연결되는 관심 표현은 없어 각도 확인이 필요합니다.')
        if post['theme'] == 'AI 정보':
            reasons.extend(practical['evidence'])
            reasons.extend(practical['flags'])
        ai_angles = {'GGzy55':'AI로 릴스·블로그·일상 콘텐츠를 직접 제작하고 작업물·시간·어려웠던 점을 기록하세요.',
                     'gat_only_you':'AI로 크립토 이슈를 조사하고 뉴스·프로젝트 콘텐츠를 정리하는 과정을 직접 검증하세요.',
                     'seyyxz':'AI로 경제 시황을 정리하거나 매매일지를 복기해보세요. 수치 원출처와 실제 판단을 확인하고 자동 매매 성과를 꾸며내지 마세요.'}
        recommendations.append({'post':post,'rankScore':score,'reasons':reasons,'angle':ai_angles[handle] if post['theme']=='AI 정보' else persona['angle'],
                                'basis':'설정한 관심사에 따른 규칙 정렬 — 성장 효과 예측이 아닙니다.'})
    return sorted(recommendations,key=lambda item:(item['rankScore'],item['post']['published']),reverse=True)[:5]


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


MATERIAL_STATUSES = ['보관', '조사 필요', '작성 중', '게시 준비', '게시 완료']


def save_material(value):
    now = time.time()
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if 'id' not in value:
            handle = value.get('handle')
            source_id = str(value.get('source_id', ''))
            if handle not in HANDLES or not source_id.isdigit():
                raise ValueError('계정과 참고 글을 확인해주세요.')
            existing = conn.execute('SELECT id FROM materials WHERE source_id=? AND handle=?', (source_id, handle)).fetchone()
            if existing:
                return existing['id']
            ob = observed_topics(conn)
            source = next((p for p in ob['posts'] + ob['growthCases'] + ob['audienceQuestions'] if p['id'] == source_id), None)
            if not source:
                row=conn.execute('SELECT * FROM post_archive WHERE post_id=?',(source_id,)).fetchone()
                source=archived_post(conn,row) if row else None
            if not source:
                raise ValueError('수집한 참고 글을 찾지 못했습니다. 피드를 다시 확인해주세요.')
            cursor = conn.execute('INSERT INTO materials(source_id,handle,source,category,angle,draft,resources,status,publish_at,post_url,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                  (source_id, handle, json.dumps(source), source['theme'], '', '', '', '보관', '', '', now))
            return cursor.lastrowid
        identifier = value.get('id')
        if not isinstance(identifier, int):
            raise ValueError('소재 ID를 확인해주세요.')
        old = conn.execute('SELECT * FROM materials WHERE id=?', (identifier,)).fetchone()
        if not old:
            raise ValueError('저장된 소재가 없습니다.')
        if old['status'] == '게시 완료':
            raise ValueError('게시 완료 소재는 기록을 보호하기 위해 수정할 수 없습니다.')
        if value.get('updated') != old['updated']:
            raise ValueError('다른 화면에서 수정됐습니다. 소재를 다시 열어주세요.')
        handle = value.get('handle')
        status = value.get('status')
        category = value.get('category')
        angle = str(value.get('angle', '')).strip()
        draft = str(value.get('draft', '')).strip()
        resources = str(value.get('resources', '')).strip()
        publish_at = str(value.get('publish_at', '')).strip()
        post_url = str(value.get('post_url', '')).strip()
        if handle not in HANDLES or status not in MATERIAL_STATUSES or category not in ['크립토', '일상', 'AI 정보']:
            raise ValueError('계정, 상태, 테마를 확인해주세요.')
        if len(angle) > 1000 or len(draft) > 3000 or len(resources) > 1000:
            raise ValueError('문구 길이를 줄여주세요.')
        if publish_at:
            try:
                datetime.strptime(publish_at, '%Y-%m-%dT%H:%M')
            except ValueError:
                raise ValueError('게시 예정 시각을 확인해주세요.') from None
        if status in ['게시 준비', '게시 완료'] and (not angle or not draft):
            raise ValueError('게시 준비에는 내 각도와 초안이 필요합니다.')
        record_id = None
        if status == '게시 완료':
            pattern = r'https://x\.com/' + re.escape(handle) + r'/status/[0-9]+(?:\?.*)?'
            if not re.fullmatch(pattern, post_url, re.IGNORECASE):
                raise ValueError('해당 관리 계정에서 직접 게시한 글의 X 링크가 필요합니다.')
            cursor = conn.execute('INSERT INTO records(handle,text,category,url,created) VALUES (?,?,?,?,?)', (handle,draft,category,post_url,now))
            record_id = cursor.lastrowid
        try:
            conn.execute('UPDATE materials SET handle=?,category=?,angle=?,draft=?,resources=?,status=?,publish_at=?,post_url=?,record_id=?,updated=? WHERE id=?',
                         (handle,category,angle,draft,resources,status,publish_at,post_url,record_id,now,identifier))
        except sqlite3.IntegrityError:
            raise ValueError('같은 글이 이 계정에 이미 저장되어 있습니다.') from None
        return identifier


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
        if urllib.parse.urlsplit(self.path).path == '/api/archive':
            try:
                params=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                self.send(200,archive_page(params.get('q',[''])[0],params.get('theme',[''])[0],int(params.get('page',['1'])[0])))
            except (ValueError,TypeError):
                self.send(400,{'error':'검색 조건을 확인해주세요.'})
        elif self.path == '/api/dashboard':
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
        if self.path not in ('/api/records', '/api/experiments', '/api/materials'):
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
            if self.path == '/api/materials':
                identifier = save_material(value)
                return self.send(201, {'ok': True, 'id': identifier})
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
        except ValueError as error:
            self.send(400, {'error': str(error) if self.path == '/api/materials' else '입력 내용을 확인해주세요.'})
        except Exception:
            self.send(400, {'error': '입력 내용을 확인해주세요.'})


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
