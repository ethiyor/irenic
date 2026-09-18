"""Quota pacing and no-retry guarantees, without Gmail traffic."""
import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from outreach_live import Gmail


class PacingTests(unittest.TestCase):
    def test_read_then_send_reserves_cost_and_waits(self):
        api = Gmail('fake')
        with patch('outreach_live.time.monotonic', side_effect=[10,10,10.1,10.4]), patch('outreach_live.time.sleep') as sleep, patch('outreach_live.urlopen', side_effect=[io.BytesIO(b'{}'),io.BytesIO(b'{}')]):
            api.get('one')
            api.send(b'approved')
        self.assertAlmostEqual(sleep.call_args_list[1].args[0], .3)
        self.assertAlmostEqual(api._next_call, 12.4)

    def test_failed_send_is_never_retried(self):
        api = Gmail('fake')
        error = HTTPError('https://gmail.googleapis.com',403,'quota',{},None)
        with patch('outreach_live.time.sleep'), patch('outreach_live.urlopen',side_effect=error) as request:
            with self.assertRaises(RuntimeError):api.send(b'approved')
            self.assertEqual(request.call_count,1)


if __name__ == '__main__':unittest.main()
