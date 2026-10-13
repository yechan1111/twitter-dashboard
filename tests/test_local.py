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
        self.assertEqual(self.app.classify_theme('AI 배우는 방법과 활용 팁 정리'), 'AI 정보')
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
            {'id':'13','text':'AI 도구 활용 방법 정리', 'viewCount':5000, 'author':{'followers':5000}},
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
        raw={'id':'876','text':'AI 활용 방법 정보 정리','viewCount':3500,'likeCount':None,
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
        raw={'id':'98765','text':'AI 정보 도구 활용 팁','viewCount':4000,'author':{'followers':2000}}
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
        with self.app.db() as conn:conn.execute('INSERT INTO cache VALUES (?,?,?)',('search:ai',json.dumps({'tweets':[{'id':'12','text':'AI 활용 정보','viewCount':4000,'author':{'followers':2000}}]}),100))
        identifier=self.app.save_material({'source_id':'12','handle':'seyyxz'})
        material=self.app.snapshot()['materials'][0]
        with self.assertRaises(ValueError):self.app.save_material({**material,'status':'게시 준비'})
        with self.assertRaises(ValueError):self.app.save_material({'source_id':'999999','handle':'seyyxz'})

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
