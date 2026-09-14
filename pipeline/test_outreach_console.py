import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from outreach_console import Console, action, initialize_demo, read_status


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        initialize_demo(self.folder)

    def tearDown(self):
        self.temp.cleanup()

    def test_demo_end_to_end_no_duplicate_and_no_expansion(self):
        action(self.folder, True, {'command': 'demo_tick'})
        action(self.folder, True, {'command': 'demo_reply'})
        action(self.folder, True, {'command': 'demo_tick'})
        action(self.folder, True, {'command': 'demo_tick'})
        s = read_status(self.folder, True)
        self.assertEqual(len(s['jobs']), 2)
        self.assertEqual(len(s['incoming']), 1)
        self.assertEqual(sum(r['enabled'] for r in s['requests']), 1)
        self.assertTrue(all(j['state'] == 'sent' for j in s['jobs']))
        action(self.folder, True, {'command': 'classify', 'message': s['incoming'][0]['id'],
                                 'kind': 'reply', 'note': 'Reviewed simulated reply'})
        self.assertEqual(read_status(self.folder, True)['incoming'][0]['kind'], 'reply')
        with self.assertRaises(ValueError):
            action(self.folder, True, {'command': 'expand', 'note': 'test'})

    def test_live_mode_rejects_demo_send_and_missing_notes(self):
        for command in ['demo_tick', 'demo_reply', 'tick', 'expand']:
            with self.assertRaises(ValueError):
                action(self.folder, False, {'command': command})
        with self.assertRaises(ValueError):
            action(self.folder, False, {'command': 'pause'})
        action(self.folder, False, {'command': 'pause', 'note': 'Reviewed operator pause'})
        self.assertTrue(read_status(self.folder)['paused'])
        self.assertEqual(read_status(self.folder)['jobs'], [])

    def test_missing_read_does_not_create_database(self):
        missing = self.folder/'missing'
        with self.assertRaises(Exception):
            read_status(missing)
        self.assertFalse(missing.exists())
        with self.assertRaises(ValueError):
            initialize_demo(self.folder)

    def test_http_authorization_origin_and_response_fields(self):
        server = Console(self.folder, True, 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            def get(headers=None, data=None):
                req = Request(server.origin+('/api/action' if data else '/api/status'),
                              headers=headers or {}, data=json.dumps(data).encode() if data else None)
                return urlopen(req, timeout=3)
            with self.assertRaises(HTTPError) as e:
                get()
            self.assertEqual(e.exception.code, 403)
            headers = {'Authorization': 'Bearer '+server.token, 'Content-Type': 'application/json'}
            with get(headers) as r:
                result = json.load(r)
                self.assertEqual(result['mode'], 'SIMULATION')
                self.assertNotIn('token', result)
                self.assertEqual(r.headers['Cache-Control'], 'no-store')
            for extra in [{'Origin': 'https://attacker.example'}, {'Host': 'attacker.example'}]:
                with self.assertRaises(HTTPError) as e:
                    get(headers | extra, {'command': 'pause', 'note': 'bad origin'})
                self.assertEqual(e.exception.code, 403)
            self.assertFalse(read_status(self.folder)['paused'])
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
