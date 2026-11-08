"""Capture transport reports size errors to waiting commands without leaking data."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from gev import live


def test_large_result_completes_command_and_logs_only_size(caplog):
    class Relay:
        received = None
        def result(self, cid, payload):
            self.received = (cid, payload)
    relay = Relay()
    server = ThreadingHTTPServer(('127.0.0.1', 0), live.make_handler(relay, 'test-only'))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    caplog.set_level('INFO', logger='gev.live')
    try:
        data = json.dumps({'id': 7, 'payload': {'dataUrl': 'x' * live.MAX_BODY}}).encode()
        req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/result', data,
            {'Authorization': 'Bearer test-only', 'X-Hermes-Command-Id': '7'})
        try:
            urllib.request.urlopen(req)
            assert False, 'oversized upload must fail'
        except urllib.error.HTTPError as e:
            assert e.code == 413
            assert json.load(e)['code'] == 'result_too_large'
        assert relay.received[0] == 7
        assert relay.received[1]['code'] == 'result_too_large'
        assert relay.received[1]['resultBytes'] == len(data)
        assert f'bytes={len(data)}' in caplog.text
        assert 'dataUrl' not in caplog.text
    finally:
        server.shutdown()
        server.server_close()
