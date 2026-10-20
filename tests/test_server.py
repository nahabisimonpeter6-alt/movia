"""Integration tests against an isolated HTTP server and temporary SQLite database."""
import http.cookiejar
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from urllib.parse import quote

import server


class QuietHandler(server.Handler):
    def log_message(self, *_args):
        pass


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_data = server.DATA
        server.DATA = Path(self.temp.name)
        server.AUTH_ATTEMPTS.clear()
        server.initialize()
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.http.server_port}'
        self.client = self.new_client()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        server.DATA = self.original_data
        self.temp.cleanup()

    def new_client(self):
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path, method='GET', data=None, client=None, headers=None, raw=False):
        headers = dict(headers or {})
        if data is not None and not raw:
            data = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.base+path, method=method, data=data, headers=headers)
        try:
            response = (client or self.client).open(req)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read()
            if response.headers.get_content_type() == 'application/json' and body:
                body = json.loads(body)
            return response.status, body, response.headers

    def register(self, name='viewer', client=None):
        status, result, headers = self.request('/api/register', 'POST', {'username': name, 'password': 'movie-night-123'}, client)
        self.assertEqual(status, 200, result)
        return result, headers

    def film(self, **changes):
        result = dict(title='Test film', year=2026, genre='Drama', description='A test.', image='', video_url='https://example.com/film.mp4', rights=True)
        result.update(changes)
        return result

    def test_public_catalog_and_static_files(self):
        status, body, _ = self.request('/api/bootstrap')
        self.assertEqual(status, 200)
        self.assertEqual(len(body['movies']), 4)
        self.assertIsNone(body['user'])
        for asset in ['/', '/app.js', '/styles.css', '/assets/favicon.svg']:
            status, body, headers = self.request(asset)
            self.assertEqual(status, 200, asset)
            self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        self.assertEqual(self.request('/%2e%2e/server.py')[0], 404)
        self.assertEqual(self.request('/catalog.json')[0], 404)

    def test_registration_hashing_login_logout_and_session_persistence(self):
        result, headers = self.register()
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        with server.connect() as db:
            user = db.execute('SELECT * FROM users').fetchone()
            self.assertNotEqual(user['password'], 'movie-night-123')
            self.assertEqual(len(user['salt']), 32)
        server.initialize()
        self.assertEqual(self.request('/api/bootstrap')[1]['user']['username'], 'viewer')
        self.assertEqual(self.request('/api/logout', 'POST', {})[0], 200)
        self.assertIsNone(self.request('/api/bootstrap')[1]['user'])
        self.assertEqual(self.request('/api/login', 'POST', {'username': 'viewer', 'password': 'incorrect-password'})[0], 401)
        self.assertEqual(self.request('/api/login', 'POST', {'username': 'VIEWER', 'password': 'movie-night-123'})[0], 200)
        self.assertEqual(self.request('/api/register', 'POST', {'username': 'viewer', 'password': 'movie-night-123'})[0], 409)

    def test_watchlist_and_progress_persist_and_are_private(self):
        self.register()
        self.assertEqual(self.request('/api/watchlist/sintel', 'PUT', {})[0], 200)
        self.assertEqual(self.request('/api/watchlist/sintel', 'PUT', {})[0], 200)
        self.assertEqual(self.request('/api/progress/sintel', 'PUT', {'position': 45, 'duration': 888})[0], 200)
        server.initialize()
        data = self.request('/api/bootstrap')[1]
        self.assertEqual(data['watchlist'], ['sintel'])
        self.assertEqual(data['progress']['sintel']['position'], 45)
        stranger = self.new_client()
        self.register('stranger', stranger)
        self.assertEqual(self.request('/api/bootstrap', client=stranger)[1]['watchlist'], [])
        self.assertEqual(self.request('/api/bootstrap', client=stranger)[1]['progress'], {})
        self.assertEqual(self.request('/api/watchlist/missing', 'PUT', {})[0], 404)
        self.assertEqual(self.request('/api/watchlist/sintel', 'DELETE', {})[0], 200)
        self.assertEqual(self.request('/api/bootstrap')[1]['watchlist'], [])

    def test_auth_csrf_host_and_validation(self):
        self.assertEqual(self.request('/api/watchlist/sintel', 'PUT', {})[0], 401)
        self.assertEqual(self.request('/api/register', 'POST', {'username': 'valid', 'password': 'short'})[0], 400)
        self.assertEqual(self.request('/api/bootstrap', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('/api/register', 'POST', {}, headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('/api/register', 'POST', {}, headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.register()
        for data in [{'position': -1, 'duration': 10}, {'position': 20, 'duration': 10}, {'position': 0, 'duration': 0}, {'position': float('nan'), 'duration': 20}]:
            self.assertEqual(self.request('/api/progress/sintel', 'PUT', data)[0], 400)
        self.assertEqual(self.request('/api/movies', 'POST', self.film(video_url='javascript:alert(1)'))[0], 400)
        self.assertEqual(self.request('/api/movies', 'POST', self.film(rights=False))[0], 400)

    def test_private_movie_creation_deletion_and_cascades(self):
        self.register()
        status, result, _ = self.request('/api/movies', 'POST', self.film(title='<script>alert(1)</script>'))
        self.assertEqual(status, 201)
        film_id = result['movie']['id']
        self.assertEqual(len(self.request('/api/bootstrap')[1]['movies']), 5)
        self.assertEqual(len(self.request('/api/bootstrap', client=self.new_client())[1]['movies']), 4)
        stranger = self.new_client()
        self.register('stranger', stranger)
        self.assertEqual(self.request(f'/api/movies/{film_id}', 'DELETE', {}, stranger)[0], 404)
        self.request(f'/api/watchlist/{film_id}', 'PUT', {})
        self.request(f'/api/progress/{film_id}', 'PUT', {'position': 2, 'duration': 20})
        self.assertEqual(self.request('/api/movies/sintel', 'DELETE', {})[0], 403)
        self.assertEqual(self.request(f'/api/movies/{film_id}', 'DELETE', {})[0], 200)
        data = self.request('/api/bootstrap')[1]
        self.assertNotIn(film_id, data['watchlist'])
        self.assertNotIn(film_id, data['progress'])

    def test_upload_private_streaming_range_and_file_deletion(self):
        self.register()
        metadata = self.film(filename='sample.mp4')
        payload = b'0123456789abcdef'
        status, result, _ = self.request('/api/upload', 'POST', payload, headers={'X-Movia-Metadata': quote(json.dumps(metadata))}, raw=True)
        self.assertEqual(status, 201, result)
        movie = result['movie']
        self.assertEqual(self.request(movie['video_url'])[1], payload)
        status, body, headers = self.request(movie['video_url'], headers={'Range':'bytes=3-7'})
        self.assertEqual((status, body), (206, b'34567'))
        self.assertEqual(headers['Content-Range'], 'bytes 3-7/16')
        self.assertEqual(self.request(movie['video_url'], headers={'Range':'bytes=-4'})[1], b'cdef')
        self.assertEqual(self.request(movie['video_url'], headers={'Range':'bytes=12-'})[1], b'cdef')
        self.assertEqual(self.request(movie['video_url'], 'HEAD')[1], b'')
        for value in ['bytes=99-', 'bytes=8-3', 'bytes=-0', 'bytes=0-1,5-9', 'bytes=-']:
            self.assertEqual(self.request(movie['video_url'], headers={'Range':value})[0], 416)
        self.assertEqual(self.request(movie['video_url'], client=self.new_client())[0], 401)
        stranger = self.new_client()
        self.register('stranger', stranger)
        self.assertEqual(self.request(movie['video_url'], client=stranger)[0], 404)
        path = server.DATA / 'media' / Path(movie['video_url']).name
        self.assertTrue(path.exists())
        self.request('/api/movies/'+movie['id'], 'DELETE', {})
        self.assertFalse(path.exists())

    def test_invalid_uploads_leave_no_files(self):
        self.register()
        for filename in ['script.html', 'video.exe', '']:
            status, _, _ = self.request('/api/upload', 'POST', b'123', headers={'X-Movia-Metadata': quote(json.dumps(self.film(filename=filename)))}, raw=True)
            self.assertEqual(status, 400)
        self.assertEqual(list((server.DATA/'media').iterdir()), [])

    def test_login_rate_limit(self):
        server.AUTH_ATTEMPTS['127.0.0.1'].extend([server.time.time()] * 12)
        self.assertEqual(self.request('/api/login', 'POST', {'username':'viewer','password':'movie-night-123'})[0], 429)


if __name__ == '__main__':
    unittest.main()
