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
            self.assertEqual(api.call_count, 8)
            self.assertFalse(self.app.refresh())
            self.assertEqual(api.call_count, 8)
            result = self.app.snapshot()
            self.assertEqual(len(result['accounts'][0]['history']), 1)
            self.assertEqual(result['observation']['sampleSize'], 1)
            self.assertEqual(result['observation']['topics'][0]['count'], 1)
            self.assertNotIn('not-a-real-key', json.dumps(result))
            self.app.snapshot()
            self.assertEqual(api.call_count, 8)  # Reading the UI makes no provider calls.
            self.app.refresh(force=True)
            self.assertEqual(api.call_count, 13)  # Profiles are only fetched once/day.
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
        for index, (followers, views) in enumerate([(1000,10000),(999,10000),(1000,9999),
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
        self.assertEqual(result['filters'],{'minFollowers':1000,'minViews':10000})

    def test_slot_before_morning_and_after_evening(self):
        morning = datetime(2026, 10, 13, 8, 0, tzinfo=self.app.KST)
        self.assertEqual(self.app.slot(morning), '2026-10-12T21:00:00+09:00')
        evening = datetime(2026, 10, 13, 21, 5, tzinfo=self.app.KST)
        self.assertEqual(self.app.slot(evening), '2026-10-13T21:00:00+09:00')

    def test_cap_prevents_network_call(self):
        month = datetime.now(self.app.KST).strftime('%Y-%m')
        with self.app.db() as conn:
            conn.executemany('INSERT INTO requests VALUES (?,?)', [(month, 1)] * 600)
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
