import importlib.util
import json
import os
import plistlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

class LocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vars = patch.dict(os.environ, {'SIGNAL_DATA_DIR': self.temp.name})
        self.vars.start()
        spec = importlib.util.spec_from_file_location('signal_test_server', ROOT / 'server.py')
        self.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.app)

    def tearDown(self):
        self.vars.stop()
        self.temp.cleanup()

    def fixture(self, path, handle, key, query=None):
        if '/info' in path:
            return {'data': {'id': '123', 'followers': 91}}
        post = {'id': '123456', 'text': '비트코인 공부 #Web3', 'createdAt': 'Tue Oct 13 03:00:00 +0000 2026', 'likeCount': 3, 'viewCount': 10000, 'author': {'userName': 'public_sample', 'followers': 1000}}
        return {'tweets': [post]}

    def test_refresh_history_and_observation(self):
        with patch.object(self.app, 'provider', side_effect=self.fixture) as api, patch.object(self.app, 'api_key', return_value='not-a-real-key'):
            self.assertTrue(self.app.refresh())
            self.assertEqual(api.call_count, 10)
            self.assertFalse(self.app.refresh())
            self.assertEqual(api.call_count, 10)
            result = self.app.snapshot()
            self.assertEqual(len(result['accounts'][0]['history']), 1)
            self.assertEqual(result['observation']['sampleSize'], 1)
            self.assertEqual(result['observation']['topics'][0]['count'], 1)
            self.assertNotIn('not-a-real-key', json.dumps(result))
            self.app.snapshot()
            self.assertEqual(api.call_count, 10)  # Reading the UI makes no provider calls.
            self.app.refresh(force=True)
            self.assertEqual(api.call_count, 16)  # Profiles are only fetched once/day.
            self.assertEqual(len(self.app.snapshot()['accounts'][0]['history']), 1)

    def test_failed_refresh_preserves_existing_posts(self):
        with patch.object(self.app, 'provider', side_effect=self.fixture):
            self.app.refresh(force=True)
        with patch.object(self.app, 'provider', side_effect=ValueError('데이터 제공사 오류 HTTP 402')):
            self.app.refresh(force=True)
        result = self.app.snapshot()
        self.assertEqual(result['accounts'][0]['posts'][0]['likes'], 3)
        self.assertIn('402', result['accounts'][0]['error'])
        self.assertEqual(result['observation']['sampleSize'], 1)
        self.assertIn('402', result['observation']['error'])

    def test_trend_thresholds_and_missing_metrics(self):
        posts = []
        for index, (followers, views) in enumerate([(1000,3000),(999,10000),(1000,2999),
                                                   (None,20000),(5000,None),('2000','30000'),
                                                   (True,20000),(2000,'10K')], start=1):
            posts.append({'id': str(index), 'text': '비트코인', 'viewCount': views,
                          'author': {'userName': 'author'+str(index), 'followers': followers}})
        with self.app.db() as conn:
            conn.execute('INSERT INTO cache VALUES (?,?,?)', ('search:crypto', json.dumps({'tweets':posts}), 1))
            result = self.app.observed_topics(conn)
        self.assertEqual({p['id'] for p in result['posts']}, {'1','6'})
        self.assertEqual(result['scannedSize'],8)
        self.assertEqual(result['rejectedBelow'],2)
        self.assertEqual(result['rejectedMissing'],4)
        self.assertEqual(result['topics'][0]['count'],2)
        self.assertEqual(result['filters'],{'minFollowers':1000,'minViews':3000,'strongViews':5000})

    def test_growth_insight_explains_structure_without_claiming_growth(self):
        result = self.app.growth_insight({'text': '오늘 공부한 내용 정리\n여러분은 어떻게 생각하나요?'})
        self.assertEqual(result['format'], '학습·설명형')
        self.assertEqual(len(result['reasons']), 4)
        self.assertIn('검증되지', result['basis'])
        self.assertIn('24시간', result['experiment'])

    def test_theme_and_celebrity_filter(self):
        self.assertEqual(self.app.classify_theme('AI 영상 배우는 방법과 활용 팁 정리'), 'AI 정보')
        self.assertEqual(self.app.classify_theme('오늘 커피 마신 일상'), '일상')
        self.assertIsNone(self.app.classify_theme('오늘 아이돌 팬미팅 후기'))
        self.assertIsNone(self.app.classify_theme('뉴진스 AI 활용 방법'))
        self.assertIsNone(self.app.classify_theme('배우 인터뷰 오늘 일상'))
        self.assertIsNone(self.app.classify_theme('최신 영화 음악 추천'))
        self.assertIsNone(self.app.classify_theme('AI 이야기'))

    def test_growth_cases_questions_and_old_cache_excluded(self):
        posts = [
            {'id':'11','text':'오늘 커피 일상 어떻게 보내세요?', 'viewCount':500,
             'author':{'followers':150}, 'createdAt':'Tue Oct 13 03:00:00 +0000 2026'},
            {'id':'12','text':'오늘 아이돌 팬미팅', 'viewCount':50000, 'author':{'followers':5000}},
            {'id':'13','text':'AI 영상 도구 활용 방법 정리', 'viewCount':5000, 'author':{'followers':5000}},
        ]
        with self.app.db() as conn:
            conn.execute('INSERT INTO cache VALUES (?,?,?)',('search:ai',json.dumps({'tweets':posts}),1))
            conn.execute('INSERT INTO cache VALUES (?,?,?)',('search:culture',json.dumps({'tweets':[{'id':'99','text':'일상 오늘','viewCount':99999,'author':{'followers':99999}}]}),1))
            data=self.app.observed_topics(conn)
        self.assertEqual({p['id'] for p in data['posts']},{'13'})
        self.assertEqual(data['growthCases'][0]['id'],'11')
        self.assertEqual(data['audienceQuestions'][0]['id'],'11')
        self.assertEqual(data['rejectedTheme'],1)

    def test_experiment_preserves_unknown_metrics_and_updates_window(self):
        with self.app.db() as conn:
            cursor=conn.execute('INSERT INTO records(handle,text,category,url,created) VALUES (?,?,?,?,?)',('GGzy55','AI tip','AI 정보','',1))
            record=cursor.lastrowid
        value={'record_id':record,'window':'24h','views':100,'replies':0,'visits':None,'follows':None,'note':'첫 문장 실험'}
        self.app.save_experiment(value)
        data=self.app.snapshot()['records'][0]['experiments'][0]
        self.assertEqual(data['views'],100)
        self.assertIsNone(data['visits'])
        self.assertEqual(data['replies'],0)
        self.app.save_experiment({**value,'views':200})
        self.assertEqual(len(self.app.snapshot()['records'][0]['experiments']),1)
        self.assertEqual(self.app.snapshot()['records'][0]['experiments'][0]['views'],200)
        with self.assertRaises(ValueError):self.app.save_experiment({**value,'record_id':999})
        with self.assertRaises(ValueError):self.app.save_experiment({**value,'views':-1})

    def test_post_structure_separates_hook_and_format(self):
        raw={'text':'여러분은 AI 어떻게 활용하나요?\n방법과 팁을 정리했습니다.', 'extendedEntities':{'media':[{'type':'photo'}]}}
        result=self.app.post_structure(raw)
        self.assertEqual(result['firstSentence'],'여러분은 AI 어떻게 활용하나요?')
        self.assertIn('질문형 후보',result['hooks'])
        self.assertEqual(result['format'],'사진 + 문구')
        self.assertTrue(result['hookEvidence'])
        self.assertIn('규칙',result['classificationBasis'])

    def test_observations_preserve_values_and_do_not_mutate_on_read(self):
        raw={'id':'876','text':'AI 영상 활용 방법 정보 정리','viewCount':3500,'likeCount':None,
             'replyCount':0,'retweetCount':2,'author':{'followers':1500}}
        with self.app.db() as conn:
            self.app.record_observation(conn,raw,100)
            self.app.record_observation(conn,raw,100)
            self.app.record_observation(conn,{**raw,'viewCount':3400},200)
            conn.execute('INSERT INTO cache VALUES (?,?,?)',('search:ai',json.dumps({'tweets':[raw]}),200))
            result=self.app.observed_topics(conn)
            post=result['posts'][0]
            self.assertEqual(post['collectedAt'],200)
            self.assertEqual(len(post['observations']),2)
            self.assertEqual([o['views'] for o in post['observations']],[3500,3400])
            self.assertIsNone(post['observations'][0]['likes'])
            self.app.observed_topics(conn)
            self.assertEqual(conn.execute('SELECT count(*) FROM post_observations').fetchone()[0],2)

    def test_material_lifecycle_duplicate_and_completed_record(self):
        raw={'id':'98765','text':'AI 영상 정보 도구 활용 팁','viewCount':4000,'author':{'followers':2000}}
        with self.app.db() as conn:conn.execute('INSERT INTO cache VALUES (?,?,?)',('search:ai',json.dumps({'tweets':[raw]}),100))
        identifier=self.app.save_material({'source_id':'98765','handle':'GGzy55'})
        self.assertEqual(identifier,self.app.save_material({'source_id':'98765','handle':'GGzy55'}))
        material=self.app.snapshot()['materials'][0]
        self.assertEqual(material['source']['text'],raw['text'])
        value={**material,'angle':'내 경험으로 설명','draft':'내가 직접 사용한 AI 팁','status':'게시 준비'}
        self.app.save_material(value)
        with self.assertRaisesRegex(ValueError,'다른 화면'):self.app.save_material(value)
        material=self.app.snapshot()['materials'][0]
        with self.assertRaises(ValueError):self.app.save_material({**material,'status':'게시 완료','post_url':'https://x.com/wrong/status/123'})
        self.app.save_material({**material,'status':'게시 완료','post_url':'https://x.com/GGzy55/status/123'})
        result=self.app.snapshot()
        self.assertEqual(result['materials'][0]['status'],'게시 완료')
        self.assertEqual(result['records'][0]['text'],'내가 직접 사용한 AI 팁')
        self.assertEqual(result['materials'][0]['record_id'],result['records'][0]['id'])
        with self.assertRaises(ValueError):self.app.save_material({**result['materials'][0],'status':'게시 완료'})
        self.assertEqual(len(self.app.snapshot()['records']),1)

    def test_material_ready_requires_angle_and_draft(self):
        with self.app.db() as conn:conn.execute('INSERT INTO cache VALUES (?,?,?)',('search:ai',json.dumps({'tweets':[{'id':'12','text':'AI 영상 활용 정보','viewCount':4000,'author':{'followers':2000}}]}),100))
        identifier=self.app.save_material({'source_id':'12','handle':'seyyxz'})
        material=self.app.snapshot()['materials'][0]
        with self.assertRaises(ValueError):self.app.save_material({**material,'status':'게시 준비'})
        with self.assertRaises(ValueError):self.app.save_material({'source_id':'999999','handle':'seyyxz'})

    def test_confirmed_personas_and_trading_macro_priority(self):
        self.assertEqual(self.app.PERSONAS['GGzy55']['name'],'gg')
        self.assertEqual(self.app.PERSONAS['gat_only_you']['name'],'유경')
        self.assertEqual(self.app.PERSONAS['seyyxz']['name'],'세인')
        self.assertIn('실제 트레이딩',self.app.PERSONAS['seyyxz']['focus'])
        self.assertEqual(self.app.classify_theme('FOMC 금리와 환율 경제 시황 정리'),'크립토')
        posts=[{'id':'1','theme':'크립토','text':'에어드랍 작업 참여 조건','published':1},
               {'id':'2','theme':'크립토','text':'FOMC 금리 환율 시장 현황','published':1},
               {'id':'3','theme':'일상','text':'일상 셀카 밋업 이야기','published':1}]
        self.assertEqual(self.app.recommend_posts('gat_only_you',posts)[0]['post']['id'],'1')
        self.assertEqual(self.app.recommend_posts('seyyxz',posts)[0]['post']['id'],'2')
        self.assertEqual(self.app.recommend_posts('GGzy55',posts)[0]['post']['id'],'3')

    def test_idol_vote_posts_excluded_without_blocking_normal_votes(self):
        sample='[📢] 투표 기간 동안 하루 1번 MAMA 팬스 초이스 투표 게시물을 올릴 예정입니다. 해당 게시물 꼭 RT와 답글로 해시태그 사용 부탁드립니다. #2026MAMAVOTE #idle'
        self.assertTrue(self.app.celebrity_related(sample))
        self.assertIsNone(self.app.classify_theme(sample))
        self.assertTrue(self.app.celebrity_related('오늘 #2026MAMAVOTE'))
        self.assertTrue(self.app.celebrity_related('일상 #idle'))
        self.assertFalse(self.app.celebrity_related('오늘 아이들하고 산책한 일상'))
        self.assertFalse(self.app.celebrity_related('크립토 프로젝트 DAO 투표 참여 방법'))
        self.assertFalse(self.app.celebrity_related('AI 서비스 idle 상태 활용 정보'))

    def test_ai_collection_scope_and_practical_ranking(self):
        self.assertEqual(self.app.classify_theme('AI 릴스 제작 방법과 비용 정리'),'AI 정보')
        self.assertIsNone(self.app.classify_theme('AI 최신 모델 기능 정보'))
        good={'id':'1','text':'AI 블로그 방법 직접 테스트, 3단계 과정과 비용, 한계 정리','theme':'AI 정보','published':1}
        hype={'id':'2','text':'AI 블로그 부업 정보 월천 수익보장 강의 할인코드','theme':'AI 정보','published':2}
        result=self.app.recommend_posts('GGzy55',[hype,good])
        self.assertEqual(result[0]['post']['id'],'1')
        self.assertEqual(len(self.app.practical_insight(hype['text'])['flags']),2)
        self.assertIn('작업물',result[0]['angle'])

    def test_archive_survives_cache_removal_and_preserves_newest_measurement(self):
        raw={'id':'654','text':'AI 블로그 활용 방법','viewCount':4000,'author':{'followers':2000,'userName':'writer'}}
        with self.app.db() as conn:
            self.app.record_observation(conn,raw,100)
            self.app.record_observation(conn,{**raw,'viewCount':5000},200)
            self.app.record_observation(conn,raw,50)
            conn.execute('DELETE FROM cache')
        page=self.app.archive_page('블로그','AI 정보',1)
        self.assertEqual(page['total'],1)
        self.assertEqual(page['posts'][0]['views'],5000)
        self.assertEqual(page['posts'][0]['firstSeen'],50)
        self.assertEqual(page['posts'][0]['collectedAt'],200)
        self.assertEqual(self.app.archive_page('없는단어')['total'],0)
        identifier=self.app.save_material({'source_id':'654','handle':'GGzy55'})
        self.assertEqual(self.app.snapshot()['materials'][0]['id'],identifier)
        self.assertEqual(self.app.snapshot()['materials'][0]['source']['text'],raw['text'])
        with self.assertRaises(ValueError):self.app.archive_page(page=0)

    def test_manual_account_refresh_always_fetches_profiles_without_search(self):
        with patch.object(self.app,'provider',side_effect=self.fixture) as api, patch.object(self.app,'api_key',return_value='fake'):
            result=self.app.manual_account_refresh()
            self.assertEqual(api.call_count,6)
            self.assertEqual(len(result['accounts']),3)
            self.assertFalse(result['refreshing'])
            self.app.manual_account_refresh()
            self.assertEqual(api.call_count,12)
            self.assertTrue(all(call.args[0] in ['/twitter/user/info','/twitter/user/last_tweets'] for call in api.call_args_list))
        with self.app.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM cache WHERE key LIKE 'search:%'").fetchone()[0],0)
        self.app.REFRESH_LOCK.acquire()
        try:
            with patch.object(self.app,'provider') as api:
                self.assertIsNone(self.app.manual_account_refresh())
                api.assert_not_called()
        finally:self.app.REFRESH_LOCK.release()

    def test_slot_before_morning_and_after_evening(self):
        morning = datetime(2026, 10, 13, 8, 0, tzinfo=self.app.KST)
        self.assertEqual(self.app.slot(morning), '2026-10-12T21:00:00+09:00')
        evening = datetime(2026, 10, 13, 21, 5, tzinfo=self.app.KST)
        self.assertEqual(self.app.slot(evening), '2026-10-13T21:00:00+09:00')

    def test_cap_prevents_network_call(self):
        month = datetime.now(self.app.KST).strftime('%Y-%m')
        with self.app.db() as conn:
            conn.executemany('INSERT INTO requests VALUES (?,?)', [(month, 1)] * self.app.MONTH_LIMIT)
        with patch.object(self.app.urllib.request, 'urlopen') as network:
            with self.assertRaisesRegex(ValueError, '상한'):
                self.app.provider('/twitter/user/info', 'GGzy55', 'fake')
            network.assert_not_called()

    def test_launch_agent_no_secrets_and_handles_spaces(self):
        spec = importlib.util.spec_from_file_location('mac_install', ROOT / 'install_mac.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        config = module.launch_config(Path('/Users/a/My App'), Path('/usr/bin/python3'), Path('/Users/a'))
        restored = plistlib.loads(plistlib.dumps(config))
        self.assertEqual(restored['ProgramArguments'][1], '/Users/a/My App/server.py')
        self.assertTrue(restored['RunAtLoad'])
        self.assertTrue(restored['KeepAlive'])
        self.assertNotIn('EnvironmentVariables', restored)

if __name__ == '__main__':
    unittest.main()
