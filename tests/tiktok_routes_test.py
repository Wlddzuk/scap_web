import os
import shutil
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch


TEST_DIR = tempfile.mkdtemp(prefix='clipper-tiktok-routes-')
os.environ['DATABASE_URI'] = f"sqlite:///{os.path.join(TEST_DIR, 'test.db')}"
os.environ['FLASK_SECRET_KEY'] = 'test-flask-secret'
os.environ['TIKTOK_TOKEN_ENCRYPTION_KEY'] = 'test-token-secret'
os.environ['TIKTOK_CLIENT_KEY'] = 'test-client-key'
os.environ['TIKTOK_CLIENT_SECRET'] = 'test-client-secret'
os.environ['TIKTOK_REDIRECT_URI'] = 'https://clipper.example/api/tiktok/oauth/callback'
os.environ['TIKTOK_ALLOW_PUBLIC_POSTS'] = 'false'
os.environ['SESSION_COOKIE_SECURE'] = 'false'

import app as clipper_app
from models import Article, TikTokAccount, db


TOKEN_DATA = {
    'open_id': 'creator-open-id',
    'access_token': 'access-token-secret',
    'refresh_token': 'refresh-token-secret',
    'expires_in': 86_400,
    'refresh_expires_in': 31_536_000,
    'scope': 'user.info.basic,video.publish',
}


class TikTokRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        clipper_app.app.config.update(TESTING=True)
        cls.client = clipper_app.app.test_client()

    @classmethod
    def tearDownClass(cls):
        with clipper_app.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(TEST_DIR)

    def setUp(self):
        with clipper_app.app.app_context():
            db.session.query(Article).delete()
            db.session.query(TikTokAccount).delete()
            db.session.commit()

    def _connect_account(self):
        start = self.client.get('/api/tiktok/oauth/start')
        location = start.headers['Location']
        state = parse_qs(urlparse(location).query)['state'][0]
        with patch.object(clipper_app, 'tiktok_exchange_code', return_value=TOKEN_DATA), \
             patch.object(clipper_app, '_refresh_creator_info'):
            callback = self.client.get(
                f'/api/tiktok/oauth/callback?code=auth-code&state={state}'
            )
        self.assertEqual(callback.status_code, 302)
        self.assertTrue(callback.headers['Location'].endswith('/?tiktok=connected'))

    def test_oauth_start_uses_exact_redirect_and_required_scopes(self):
        response = self.client.get('/api/tiktok/oauth/start')
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response.headers['Location']).query)
        self.assertEqual(query['client_key'], ['test-client-key'])
        self.assertEqual(query['redirect_uri'], ['https://clipper.example/api/tiktok/oauth/callback'])
        self.assertEqual(query['scope'], ['user.info.basic,video.publish'])
        self.assertTrue(query['state'][0])

    def test_callback_stores_encrypted_tokens_without_exposing_them(self):
        self._connect_account()
        response = self.client.get('/api/tiktok/status')
        payload = response.get_json()
        self.assertTrue(payload['connected'])
        self.assertNotIn('access_token', payload)
        self.assertNotIn('refresh_token', payload)
        with clipper_app.app.app_context():
            account = TikTokAccount.query.one()
            self.assertNotIn('access-token-secret', account.access_token_encrypted)
            self.assertNotIn('refresh-token-secret', account.refresh_token_encrypted)

    def test_publish_requires_explicit_consent(self):
        with clipper_app.app.app_context():
            article = Article(url='https://example.test/one', title='Story', content='Body')
            db.session.add(article)
            db.session.commit()
            article_id = article.id
        response = self.client.post(
            f'/api/articles/{article_id}/tiktok/publish',
            json={'title': 'Caption', 'privacy_level': 'SELF_ONLY'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('consent', response.get_json()['error'].lower())

    def test_private_publish_initializes_upload_and_tracks_publish_id(self):
        self._connect_account()
        handle, video_path = tempfile.mkstemp(suffix='.mp4', dir=TEST_DIR)
        with os.fdopen(handle, 'wb') as video:
            video.write(b'fake-mp4-content')

        with clipper_app.app.app_context():
            article = Article(
                url='https://example.test/two',
                title='Science story',
                content='Body',
                video_path='fake.mp4',
            )
            db.session.add(article)
            db.session.commit()
            article_id = article.id
            account = TikTokAccount.query.one()

        creator = {
            'creator_nickname': '60s Science',
            'privacy_level_options': ['SELF_ONLY'],
            'comment_disabled': False,
            'duet_disabled': False,
            'stitch_disabled': False,
            'max_video_post_duration_sec': 300,
        }
        initialized = {'publish_id': 'pub-test-1', 'upload_url': 'https://upload.example/video'}
        with patch.object(clipper_app, '_video_file_for_article', return_value=video_path), \
             patch.object(clipper_app, '_video_duration_seconds', return_value=20.0), \
             patch.object(clipper_app, '_refresh_creator_info', return_value=('access', account, creator)), \
             patch.object(clipper_app, 'initialize_video_post', return_value=initialized) as init_post, \
             patch.object(clipper_app, 'upload_video_file') as upload:
            response = self.client.post(
                f'/api/articles/{article_id}/tiktok/publish',
                json={
                    'title': 'Science story #science',
                    'privacy_level': 'SELF_ONLY',
                    'allow_comment': False,
                    'allow_duet': False,
                    'allow_stitch': False,
                    'brand_content_toggle': False,
                    'brand_organic_toggle': False,
                    'consent': True,
                },
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()['publish_id'], 'pub-test-1')
        self.assertTrue(init_post.called)
        self.assertTrue(upload.called)
        with clipper_app.app.app_context():
            saved = db.session.get(Article, article_id)
            self.assertEqual(saved.tiktok_publish_status, 'PROCESSING_UPLOAD')
            self.assertEqual(saved.tiktok_publish_id, 'pub-test-1')


if __name__ == '__main__':
    unittest.main()
