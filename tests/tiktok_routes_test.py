import asyncio
import json
import os
import shutil
import tempfile
import threading
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, Mock, patch


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
from models import Article, PlatformPost, PublisherAccount, TikTokAccount, VideoMetrics, db
from publishers import PublishResult, PublisherError


TOKEN_DATA = {
    'open_id': 'creator-open-id',
    'access_token': 'access-token-secret',
    'refresh_token': 'refresh-token-secret',
    'expires_in': 86_400,
    'refresh_expires_in': 31_536_000,
    'scope': 'user.info.basic,video.publish,video.list,user.info.stats',
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
            db.session.query(PlatformPost).delete()
            db.session.query(PublisherAccount).delete()
            db.session.query(Article).delete()
            db.session.query(TikTokAccount).delete()
            db.session.commit()

    def test_unified_publish_keeps_success_when_one_platform_fails(self):
        with clipper_app.app.app_context():
            article = Article(
                url='https://example.test/multi-platform-partial',
                title='A science short',
                content='Body',
                status='video_done',
                video_path='short.mp4',
            )
            db.session.add(article)
            db.session.commit()
            article_id = article.id

        class FakePublisher:
            def __init__(self, platform):
                self.platform = platform

            def publish(self, _article, _video_path, _options):
                if self.platform == 'instagram':
                    raise PublisherError('Instagram is unavailable')
                return PublishResult(
                    self.platform,
                    'PUBLISHED',
                    True,
                    external_id='youtube-video-1',
                    permalink='https://youtube.test/shorts/youtube-video-1',
                )

        with patch.object(
            clipper_app,
            '_video_file_for_article',
            return_value='/tmp/short.mp4',
        ), patch.object(
            clipper_app,
            '_make_publisher',
            side_effect=lambda platform, _article: FakePublisher(platform),
        ):
            response = self.client.post(
                f'/api/articles/{article_id}/publish',
                json={
                    'platforms': ['instagram', 'youtube'],
                    'caption': 'Shared caption',
                    'options': {'youtube': {'title': 'Short title'}},
                },
            )

        self.assertEqual(response.status_code, 207)
        payload = response.get_json()
        self.assertFalse(payload['all_accepted'])
        self.assertEqual(payload['results']['instagram']['status'], 'FAILED')
        self.assertEqual(payload['results']['youtube']['status'], 'PUBLISHED')
        with clipper_app.app.app_context():
            posts = {
                post.platform: post
                for post in PlatformPost.query.filter_by(article_id=article_id).all()
            }
            self.assertEqual(posts['instagram'].status, 'FAILED')
            self.assertEqual(posts['youtube'].status, 'PUBLISHED')
            self.assertEqual(posts['youtube'].external_id, 'youtube-video-1')

    def test_unified_publish_is_idempotent_per_successful_platform(self):
        with clipper_app.app.app_context():
            article = Article(
                url='https://example.test/multi-platform-idempotent',
                title='A science short',
                content='Body',
                status='video_done',
                video_path='short.mp4',
            )
            db.session.add(article)
            db.session.flush()
            article_id = article.id
            db.session.add(PlatformPost(
                article_id=article_id,
                platform='youtube',
                status='PUBLISHED',
                external_id='already-there',
                permalink='https://youtube.test/shorts/already-there',
            ))
            db.session.commit()

        with patch.object(
            clipper_app,
            '_video_file_for_article',
            return_value='/tmp/short.mp4',
        ), patch.object(clipper_app, '_make_publisher') as make_publisher:
            response = self.client.post(
                f'/api/articles/{article_id}/publish',
                json={'platforms': ['youtube'], 'caption': 'Caption'},
            )

        self.assertEqual(response.status_code, 202)
        result = response.get_json()['results']['youtube']
        self.assertTrue(result['accepted'])
        self.assertTrue(result['idempotent'])
        self.assertEqual(result['external_id'], 'already-there')
        make_publisher.assert_not_called()

    def test_publisher_status_never_serializes_oauth_tokens(self):
        with clipper_app.app.app_context():
            cipher = clipper_app._oauth_cipher()
            account = PublisherAccount(
                platform='youtube',
                external_user_id='channel-1',
                username='60s Science',
                access_token_encrypted=cipher.encrypt('youtube-access-secret'),
                refresh_token_encrypted=cipher.encrypt('youtube-refresh-secret'),
                scope='https://www.googleapis.com/auth/youtube.upload',
            )
            db.session.add(account)
            db.session.commit()

        response = self.client.get('/api/publishers/status')

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertNotIn('youtube-access-secret', body)
        self.assertNotIn('youtube-refresh-secret', body)
        self.assertTrue(response.get_json()['platforms']['youtube']['connected'])

    def test_facebook_oauth_stores_encrypted_page_token(self):
        env = {
            'FACEBOOK_APP_ID': 'facebook-app-id',
            'FACEBOOK_APP_SECRET': 'facebook-app-secret',
            'FACEBOOK_REDIRECT_URI': (
                'https://clipper.example/api/facebook/oauth/callback'
            ),
        }
        with patch.dict(os.environ, env):
            start = self.client.get('/api/facebook/oauth/start')
            self.assertEqual(start.status_code, 302)
            query = parse_qs(urlparse(start.headers['Location']).query)
            self.assertEqual(
                set(query['scope'][0].split(',')),
                {
                    'pages_show_list',
                    'pages_manage_posts',
                    'pages_read_engagement',
                },
            )
            self.assertEqual(
                query['redirect_uri'],
                ['https://clipper.example/api/facebook/oauth/callback'],
            )

            def response(payload):
                value = Mock()
                value.status_code = 200
                value.ok = True
                value.headers = {}
                value.json.return_value = payload
                return value

            responses = [
                response({'access_token': 'short-user-token'}),
                response({
                    'access_token': 'long-user-token',
                    'expires_in': 5_184_000,
                }),
                response({
                    'data': [{
                        'id': 'page-123',
                        'name': '60s Science',
                        'access_token': 'page-access-secret',
                    }],
                }),
            ]
            with patch.object(
                clipper_app,
                'request_with_retries',
                side_effect=responses,
            ):
                callback = self.client.get(
                    '/api/facebook/oauth/callback'
                    f'?code=meta-code&state={query["state"][0]}'
                )

        self.assertEqual(callback.status_code, 302)
        self.assertTrue(callback.headers['Location'].endswith('/?facebook=connected'))
        with clipper_app.app.app_context():
            account = PublisherAccount.query.filter_by(platform='facebook').one()
            self.assertEqual(account.external_user_id, 'page-123')
            self.assertEqual(account.username, '60s Science')
            self.assertNotIn(
                'page-access-secret',
                account.access_token_encrypted,
            )
            self.assertEqual(
                clipper_app._oauth_cipher().decrypt(account.access_token_encrypted),
                'page-access-secret',
            )

        body = self.client.get('/api/publishers/status').get_data(as_text=True)
        self.assertNotIn('page-access-secret', body)
        self.assertIn('"facebook"', body)

    def test_unified_facebook_publish_persists_processing_and_starts_poller(self):
        with clipper_app.app.app_context():
            article = Article(
                url='https://example.test/facebook-reel',
                title='A Facebook science Reel',
                content='Body',
                status='video_done',
                video_path='short.mp4',
            )
            db.session.add(article)
            db.session.commit()
            article_id = article.id

        class FakeFacebookPublisher:
            def publish(self, _article, _video_path, options):
                self.options = options
                return PublishResult(
                    'facebook',
                    'PROCESSING_UPLOAD',
                    True,
                    external_id='facebook-reel-1',
                )

        publisher = FakeFacebookPublisher()
        with patch.object(
            clipper_app,
            '_video_file_for_article',
            return_value='/tmp/short.mp4',
        ), patch.object(
            clipper_app,
            '_make_publisher',
            return_value=publisher,
        ), patch.object(
            clipper_app,
            '_start_facebook_reel_poller',
        ) as start_poller:
            response = self.client.post(
                f'/api/articles/{article_id}/publish',
                json={
                    'platforms': ['facebook'],
                    'caption': 'Shared science caption',
                    'options': {'facebook': {}},
                },
            )

        self.assertEqual(response.status_code, 202)
        result = response.get_json()['results']['facebook']
        self.assertEqual(result['status'], 'PROCESSING_UPLOAD')
        self.assertEqual(result['external_id'], 'facebook-reel-1')
        self.assertEqual(
            publisher.options['caption'],
            'Shared science caption',
        )
        start_poller.assert_called_once_with(article_id)
        with clipper_app.app.app_context():
            post = PlatformPost.query.filter_by(
                article_id=article_id,
                platform='facebook',
            ).one()
            self.assertEqual(post.status, 'PROCESSING_UPLOAD')
            self.assertEqual(post.external_id, 'facebook-reel-1')

    def test_schema_migration_backfills_legacy_tiktok_post(self):
        with clipper_app.app.app_context():
            article = Article(
                url='https://example.test/legacy-platform-post',
                title='A historical post',
                content='Body',
                tiktok_publish_id='legacy-publish-1',
                tiktok_publish_status='PUBLISH_COMPLETE',
            )
            db.session.add(article)
            db.session.commit()
            article_id = article.id

        clipper_app._migrate_schema()

        with clipper_app.app.app_context():
            post = PlatformPost.query.filter_by(
                article_id=article_id,
                platform='tiktok',
            ).one()
            self.assertEqual(post.external_id, 'legacy-publish-1')
            self.assertEqual(post.status, 'PUBLISHED')

    def test_public_instagram_media_url_requires_valid_signature(self):
        with patch.dict(os.environ, {
            'PUBLIC_BASE_URL': 'https://clipper.example',
            'PUBLIC_MEDIA_SIGNING_KEY': 'media-signing-secret',
        }):
            signed = clipper_app._signed_public_video_url('missing-video.mp4')
            parsed = urlparse(signed)
            accepted = self.client.get(f'{parsed.path}?{parsed.query}')
            tampered = self.client.get(
                f'{parsed.path}?expires={parse_qs(parsed.query)["expires"][0]}&sig=bad'
            )

        # A valid signature passes authorization and reaches file lookup.
        self.assertEqual(accepted.status_code, 404)
        self.assertEqual(tampered.status_code, 403)

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

    def test_oauth_start_defaults_to_posting_scopes(self):
        response = self.client.get('/api/tiktok/oauth/start')
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response.headers['Location']).query)
        self.assertEqual(query['client_key'], ['test-client-key'])
        self.assertEqual(query['redirect_uri'], ['https://clipper.example/api/tiktok/oauth/callback'])
        self.assertEqual(
            query['scope'],
            ['user.info.basic,video.publish'],
        )
        self.assertTrue(query['state'][0])

    def test_oauth_start_can_explicitly_request_metrics_scopes(self):
        with patch.dict(os.environ, {'TIKTOK_REQUEST_METRICS_SCOPES': 'true'}):
            response = self.client.get('/api/tiktok/oauth/start')

        query = parse_qs(urlparse(response.headers['Location']).query)
        self.assertEqual(
            query['scope'],
            ['user.info.basic,video.publish,video.list,user.info.stats'],
        )

    def test_posting_connection_does_not_require_metrics_reconsent(self):
        self._connect_account()
        with clipper_app.app.app_context():
            account = TikTokAccount.query.one()
            account.scope = 'user.info.basic,video.publish'
            db.session.commit()

        payload = self.client.get('/api/tiktok/status').get_json()

        self.assertFalse(payload['needs_reconsent'])
        self.assertTrue(payload['posting_authorized'])
        self.assertFalse(payload['metrics_authorized'])
        self.assertEqual(payload['missing_scopes'], [])
        self.assertEqual(payload['missing_metrics_scopes'], ['video.list', 'user.info.stats'])

    def test_metrics_opt_in_marks_existing_posting_connection_for_reconsent(self):
        self._connect_account()
        with clipper_app.app.app_context():
            account = TikTokAccount.query.one()
            account.scope = 'user.info.basic,video.publish'
            db.session.commit()

        with patch.dict(os.environ, {'TIKTOK_REQUEST_METRICS_SCOPES': 'true'}):
            payload = self.client.get('/api/tiktok/status').get_json()

        self.assertTrue(payload['needs_reconsent'])
        self.assertEqual(payload['reconsent_reason'], 'metrics')
        self.assertEqual(payload['missing_scopes'], ['video.list', 'user.info.stats'])

    def test_missing_publish_scope_requires_posting_reconsent(self):
        self._connect_account()
        with clipper_app.app.app_context():
            account = TikTokAccount.query.one()
            account.scope = 'user.info.basic'
            db.session.commit()

        payload = self.client.get('/api/tiktok/status').get_json()

        self.assertTrue(payload['needs_reconsent'])
        self.assertFalse(payload['posting_authorized'])
        self.assertEqual(payload['reconsent_reason'], 'posting')
        self.assertEqual(payload['missing_scopes'], ['video.publish'])

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
            db.session.expire_all()
            saved = db.session.get(Article, article_id)
            self.assertEqual(saved.tiktok_publish_status, 'PROCESSING_UPLOAD')
            self.assertEqual(saved.tiktok_publish_id, 'pub-test-1')

    def _publish_with_initialize_error(self, error, slug):
        handle, video_path = tempfile.mkstemp(suffix='.mp4', dir=TEST_DIR)
        with os.fdopen(handle, 'wb') as video:
            video.write(b'fake-mp4-content')

        with clipper_app.app.app_context():
            article = Article(
                url=f'https://example.test/{slug}',
                title='TikTok failure story',
                content='Body',
                video_path=f'{slug}.mp4',
            )
            db.session.add(article)
            db.session.commit()
            article_id = article.id

        creator = {
            'privacy_level_options': ['SELF_ONLY'],
            'comment_disabled': False,
            'duet_disabled': False,
            'stitch_disabled': False,
            'max_video_post_duration_sec': 300,
        }
        with patch.object(clipper_app, '_video_file_for_article', return_value=video_path), \
             patch.object(clipper_app, '_video_duration_seconds', return_value=20.0), \
             patch.object(clipper_app, '_refresh_creator_info', return_value=('access', object(), creator)), \
             patch.object(clipper_app, 'initialize_video_post', side_effect=error), \
             self.assertLogs(clipper_app.logger.name, level='ERROR') as logs:
            response = self.client.post(
                f'/api/articles/{article_id}/tiktok/publish',
                json={
                    'title': 'TikTok failure story',
                    'privacy_level': 'SELF_ONLY',
                    'consent': True,
                },
            )
        return response, article_id, '\n'.join(logs.output)

    def test_unaudited_private_account_error_is_actionable_and_fully_logged(self):
        raw_message = 'See internal content-sharing-guidelines detail'
        error = clipper_app.TikTokAPIError(
            raw_message,
            code='unaudited_client_can_only_post_to_private_accounts',
            status_code=403,
            log_id='tiktok-log-private-1',
        )

        response, article_id, logs = self._publish_with_initialize_error(
            error,
            'private-account-required',
        )

        self.assertEqual(response.status_code, 403)
        payload = response.get_json()
        self.assertIn('account that is set to Private', payload['error'])
        self.assertIn('Only you (SELF_ONLY)', payload['error'])
        self.assertNotIn(raw_message, response.get_data(as_text=True))
        self.assertIn('code=unaudited_client_can_only_post_to_private_accounts', logs)
        self.assertIn('status=403', logs)
        self.assertIn('log_id=tiktok-log-private-1', logs)
        self.assertIn(f'message={raw_message}', logs)
        with clipper_app.app.app_context():
            saved = db.session.get(Article, article_id)
            self.assertEqual(saved.tiktok_publish_status, 'FAILED')
            self.assertEqual(saved.tiktok_publish_error, payload['error'])

    def test_unknown_tiktok_error_keeps_raw_detail_out_of_response_and_storage(self):
        raw_message = 'private upstream diagnostic that users must not see'
        error = clipper_app.TikTokAPIError(
            raw_message,
            code='future_private_error',
            status_code=403,
            log_id='tiktok-log-unknown-1',
        )

        response, article_id, logs = self._publish_with_initialize_error(
            error,
            'unknown-tiktok-error',
        )

        self.assertEqual(response.status_code, 403)
        self.assertNotIn(raw_message, response.get_data(as_text=True))
        self.assertEqual(
            response.get_json()['error'],
            'TikTok could not complete the request. Please try again.',
        )
        self.assertIn(f'message={raw_message}', logs)
        with clipper_app.app.app_context():
            saved = db.session.get(Article, article_id)
            self.assertEqual(
                saved.tiktok_publish_error,
                clipper_app.TIKTOK_GENERIC_PUBLISH_ERROR,
            )
            self.assertNotIn(raw_message, saved.tiktok_publish_error)

    def test_tiktok_posting_caps_have_safe_actionable_messages(self):
        expected_phrases = {
            'spam_risk_too_many_posts': '24-hour API posting cap',
            'spam_risk_too_many_pending_share': 'pending API uploads',
            'reached_active_user_cap': 'active-user publishing cap',
            'spam_risk_user_banned_from_posting': 'blocked this account',
        }
        for code, phrase in expected_phrases.items():
            with self.subTest(code=code):
                error = clipper_app.TikTokAPIError('raw private detail', code=code)
                message = clipper_app._safe_tiktok_error_message(error, 'fallback')
                self.assertIn(phrase, message)
                self.assertNotIn('raw private detail', message)

    def test_unaudited_public_account_disables_publish_button(self):
        script = self.client.get('/static/app.js').get_data(as_text=True)

        self.assertIn("modal.dataset.tiktokAccountBlocked =", script)
        self.assertIn(
            "!publicPostingEnabled && creatorAccountAppearsPublic ? 'true' : 'false'",
            script,
        )
        self.assertIn(
            "modal.dataset.tiktokAccountBlocked === 'true'",
            script,
        )
        self.assertIn('Set the TikTok account to Private', script)

    def test_suggested_caption_matches_title_plus_hashtags(self):
        with clipper_app.app.app_context():
            article = Article(
                url='https://example.test/caption',
                title='A new science result',
                content='Body',
                hashtags=json.dumps(['#science', '#space']),
            )
            self.assertEqual(
                clipper_app.suggested_tiktok_caption(article),
                'A new science result\n\n#science #space',
            )

    def test_video_completion_never_starts_a_publish(self):
        handle, output_path = tempfile.mkstemp(suffix='.mp4', dir=TEST_DIR)
        os.close(handle)
        with clipper_app.app.app_context():
            article = Article(
                url='https://example.test/manual-only',
                title='Manual publishing only',
                content='Body',
                video_script='A complete narration.',
                status='generating_video',
                video_generation_token='manual-only-token',
            )
            db.session.add(article)
            db.session.commit()
            article_id = article.id

        with patch.object(
            clipper_app,
            'generate_video',
            return_value=output_path,
        ):
            clipper_app.run_video_in_background(
                clipper_app.app.app_context(),
                article_id,
                generation_token='manual-only-token',
            )

        with clipper_app.app.app_context():
            saved = db.session.get(Article, article_id)
            self.assertEqual(saved.status, 'video_done')
            self.assertIsNone(saved.tiktok_publish_status)
            self.assertIsNone(saved.tiktok_publish_id)
            self.assertEqual(
                PlatformPost.query.filter_by(article_id=article_id).count(),
                0,
            )
        self.assertFalse(hasattr(clipper_app, 'queue_auto_publish_for_article'))

    def test_cancel_reclaims_legacy_approval_and_preserves_history(self):
        with clipper_app.app.app_context():
            article = Article(
                url='https://example.test/reclaim-approval',
                title='Reclaim approval',
                content='Body',
                status='video_done',
                video_path='approval.mp4',
                tiktok_publish_status='AWAITING_APPROVAL',
                tiktok_approval_message_id='old-discord-message',
                tiktok_approval_requested_at=clipper_app.datetime.now(
                    clipper_app.timezone.utc
                ),
                pending_publish_request='{"platforms":["tiktok","instagram"]}',
            )
            db.session.add(article)
            db.session.flush()
            article_id = article.id
            db.session.add_all([
                PlatformPost(
                    article_id=article_id,
                    platform='tiktok',
                    status='AWAITING_APPROVAL',
                ),
                PlatformPost(
                    article_id=article_id,
                    platform='instagram',
                    status='AWAITING_APPROVAL',
                ),
                PlatformPost(
                    article_id=article_id,
                    platform='youtube',
                    status='PUBLISHED',
                    external_id='youtube-history',
                ),
            ])
            db.session.commit()

        response = self.client.post(
            f'/api/articles/{article_id}/publish/cancel'
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertCountEqual(
            payload['cancelled_platforms'],
            ['tiktok', 'instagram'],
        )
        self.assertIn('publish manually', payload['message'])
        with clipper_app.app.app_context():
            saved = db.session.get(Article, article_id)
            self.assertIsNone(saved.tiktok_publish_status)
            self.assertIsNone(saved.tiktok_approval_message_id)
            self.assertIsNone(saved.tiktok_approval_requested_at)
            self.assertIsNone(saved.pending_publish_request)
            statuses = {
                post.platform: (post.status, post.external_id)
                for post in PlatformPost.query.filter_by(
                    article_id=article_id,
                ).all()
            }
            self.assertEqual(statuses['tiktok'], ('CANCELLED', None))
            self.assertEqual(statuses['instagram'], ('CANCELLED', None))
            self.assertEqual(
                statuses['youtube'],
                ('PUBLISHED', 'youtube-history'),
            )

    def test_cancel_refuses_a_genuine_remote_upload(self):
        with clipper_app.app.app_context():
            article = Article(
                url='https://example.test/remote-upload',
                title='Remote upload',
                content='Body',
                status='video_done',
                video_path='remote.mp4',
                tiktok_publish_status='PROCESSING_UPLOAD',
                tiktok_publish_id='remote-publish-id',
            )
            db.session.add(article)
            db.session.commit()
            article_id = article.id

        response = self.client.post(
            f'/api/articles/{article_id}/publish/cancel'
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn('PROCESSING_UPLOAD', response.get_json()['error'])
        self.assertIn('cannot be cancelled', response.get_json()['error'])
        with clipper_app.app.app_context():
            saved = db.session.get(Article, article_id)
            self.assertEqual(saved.tiktok_publish_id, 'remote-publish-id')
            self.assertEqual(saved.tiktok_publish_status, 'PROCESSING_UPLOAD')

    def test_manual_route_names_waiting_approval_and_cancel_action(self):
        with clipper_app.app.app_context():
            article = Article(
                url='https://example.test/no-bypass',
                title='No bypass',
                content='Body',
                status='video_done',
                video_path='approval.mp4',
                tiktok_publish_status='AWAITING_APPROVAL',
            )
            db.session.add(article)
            db.session.commit()
            article_id = article.id

        with patch.object(clipper_app, '_video_file_for_article', return_value='/tmp/video.mp4'):
            response = self.client.post(
                f'/api/articles/{article_id}/tiktok/publish',
                json={
                    'title': 'No bypass',
                    'privacy_level': 'SELF_ONLY',
                    'consent': True,
                },
            )
        self.assertEqual(response.status_code, 409)
        self.assertIn('AWAITING_APPROVAL', response.get_json()['error'])
        self.assertIn('Cancel pending post', response.get_json()['error'])

    def test_cancelled_legacy_approval_can_be_published_manually(self):
        handle, video_path = tempfile.mkstemp(suffix='.mp4', dir=TEST_DIR)
        with os.fdopen(handle, 'wb') as video:
            video.write(b'fake-mp4-content')

        creator = {
            'privacy_level_options': ['SELF_ONLY'],
            'comment_disabled': False,
            'duet_disabled': False,
            'stitch_disabled': False,
            'max_video_post_duration_sec': 300,
        }
        initialized = {'publish_id': 'manual-1', 'upload_url': 'https://upload.example/video'}
        with clipper_app.app.app_context():
            article = Article(
                url='https://example.test/reclaimed-manual',
                title='Reclaimed manual story',
                content='Body',
                status='video_done',
                video_path='manual.mp4',
                tiktok_publish_status='AWAITING_APPROVAL',
            )
            db.session.add(article)
            db.session.commit()
            article_id = article.id

        cancelled = self.client.post(
            f'/api/articles/{article_id}/publish/cancel'
        )
        self.assertEqual(cancelled.status_code, 200)

        with clipper_app.app.app_context():
            with patch.object(clipper_app, '_video_file_for_article', return_value=video_path), \
                 patch.object(clipper_app, '_video_duration_seconds', return_value=20.0), \
                 patch.object(clipper_app, '_refresh_creator_info', return_value=('access', object(), creator)), \
                 patch.object(clipper_app, 'initialize_video_post', return_value=initialized), \
                 patch.object(clipper_app, 'upload_video_file'):
                result = clipper_app.publish_article_to_tiktok(
                    article_id,
                    {
                        'title': 'Reclaimed manual story',
                        'privacy_level': 'SELF_ONLY',
                        'consent': True,
                    },
                )

            self.assertEqual(result['publish_id'], 'manual-1')
            saved = db.session.get(Article, article_id)
            self.assertEqual(saved.tiktok_publish_status, 'PROCESSING_UPLOAD')

    def test_manual_publish_atomically_claims_unstarted_article(self):
        handle, video_path = tempfile.mkstemp(suffix='.mp4', dir=TEST_DIR)
        with os.fdopen(handle, 'wb') as video:
            video.write(b'fake-mp4-content')

        with clipper_app.app.app_context():
            article = Article(
                url='https://example.test/concurrent-publish',
                title='Concurrent publish',
                content='Body',
                status='video_done',
                video_path='concurrent.mp4',
            )
            db.session.add(article)
            db.session.commit()
            article_id = article.id

        creator = {
            'privacy_level_options': ['SELF_ONLY'],
            'comment_disabled': False,
            'duet_disabled': False,
            'stitch_disabled': False,
            'max_video_post_duration_sec': 300,
        }
        initialized = {
            'publish_id': 'concurrent-publish-1',
            'upload_url': 'https://upload.example/video',
        }
        payload = {
            'title': 'Concurrent publish',
            'privacy_level': 'SELF_ONLY',
            'consent': True,
        }
        both_loaded = threading.Barrier(2)
        outcomes = []

        def loaded_video_path(_article):
            both_loaded.wait(timeout=5)
            return video_path

        def publish_once():
            with clipper_app.app.app_context():
                try:
                    result = clipper_app.publish_article_to_tiktok(article_id, payload)
                    outcomes.append(('published', result['publish_id']))
                except clipper_app.TikTokPublishRequestError as error:
                    outcomes.append(('rejected', error.status_code))

        with patch.object(
            clipper_app,
            '_video_file_for_article',
            side_effect=loaded_video_path,
        ), patch.object(
            clipper_app,
            '_video_duration_seconds',
            return_value=20.0,
        ), patch.object(
            clipper_app,
            '_refresh_creator_info',
            return_value=('access', object(), creator),
        ), patch.object(
            clipper_app,
            'initialize_video_post',
            return_value=initialized,
        ) as initialize, patch.object(clipper_app, 'upload_video_file') as upload:
            workers = [threading.Thread(target=publish_once) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=10)

        self.assertCountEqual(
            outcomes,
            [('published', 'concurrent-publish-1'), ('rejected', 409)],
        )
        initialize.assert_called_once()
        upload.assert_called_once()

    def test_status_poller_advances_processing_upload(self):
        with clipper_app.app.app_context():
            article = Article(
                url='https://example.test/poller',
                title='Poller story',
                content='Body',
                tiktok_publish_id='publish-123',
                tiktok_publish_status='PROCESSING_UPLOAD',
            )
            db.session.add(article)
            db.session.commit()
            article_id = article.id

            with patch.object(
                clipper_app,
                '_tiktok_access_token',
                return_value=('access-token', object()),
            ), patch.object(
                clipper_app,
                'fetch_publish_status',
                return_value={'status': 'PUBLISH_COMPLETE'},
            ) as fetch:
                clipper_app.poll_tiktok_publish_statuses_once()

            saved = db.session.get(Article, article_id)
            self.assertEqual(saved.tiktok_publish_status, 'PUBLISH_COMPLETE')
            self.assertIsNotNone(saved.tiktok_published_at)
            fetch.assert_called_once_with('access-token', 'publish-123')

    def test_publish_failure_reason_is_generic_in_storage_and_response(self):
        with clipper_app.app.app_context():
            article = Article(
                url='https://example.test/generic-publish-error',
                title='Generic publish error',
                content='Body',
                tiktok_publish_id='publish-failed',
                tiktok_publish_status='PROCESSING_UPLOAD',
            )
            db.session.add(article)
            db.session.commit()
            article_id = article.id

        private_reason = 'moderation_rule_internal_detail'
        with patch.object(
            clipper_app,
            '_tiktok_access_token',
            return_value=('access-token', object()),
        ), patch.object(
            clipper_app,
            'fetch_publish_status',
            return_value={'status': 'FAILED', 'fail_reason': private_reason},
        ):
            response = self.client.post(
                f'/api/articles/{article_id}/tiktok/status'
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(
            payload['article']['tiktok_publish_error'],
            clipper_app.TIKTOK_GENERIC_PUBLISH_ERROR,
        )
        self.assertEqual(
            payload['tiktok']['fail_reason'],
            clipper_app.TIKTOK_GENERIC_PUBLISH_ERROR,
        )
        self.assertNotIn(private_reason, response.get_data(as_text=True))

    def test_status_poller_loop_contains_unexpected_iteration_failure(self):
        def fail_iteration():
            clipper_app._tiktok_poller_stop.set()
            raise RuntimeError('transient database failure')

        clipper_app._tiktok_poller_stop.clear()
        try:
            with patch.object(
                clipper_app,
                'poll_tiktok_publish_statuses_once',
                side_effect=fail_iteration,
            ) as poll, patch.object(clipper_app.logger, 'error') as log_error:
                clipper_app._tiktok_status_poller_loop()
        finally:
            clipper_app._tiktok_poller_stop.clear()

        poll.assert_called_once_with()
        log_error.assert_called_once()

    def test_startup_cleanup_is_idempotent_and_preserves_published_history(self):
        with clipper_app.app.app_context():
            stale = Article(
                url='https://example.test/stale-approval',
                title='Stale approval',
                content='Body',
                status='video_done',
                video_path='stale.mp4',
                tiktok_publish_status='AWAITING_APPROVAL',
                pending_publish_request='{"platforms":["tiktok"]}',
            )
            published = Article(
                url='https://example.test/published-history',
                title='Published history',
                content='Body',
                status='video_done',
                video_path='published.mp4',
                tiktok_publish_status='PUBLISH_COMPLETE',
                tiktok_publish_id='published-remote-id',
            )
            db.session.add_all([stale, published])
            db.session.flush()
            stale_id = stale.id
            published_id = published.id
            db.session.add_all([
                PlatformPost(
                    article_id=stale_id,
                    platform='tiktok',
                    status='AWAITING_APPROVAL',
                ),
                PlatformPost(
                    article_id=published_id,
                    platform='tiktok',
                    status='PUBLISHED',
                    external_id='published-remote-id',
                ),
            ])
            db.session.commit()

        first = clipper_app._clear_legacy_awaiting_approvals()
        second = clipper_app._clear_legacy_awaiting_approvals()

        self.assertEqual(first, {'articles': 1, 'platforms': 1})
        self.assertEqual(second, {'articles': 0, 'platforms': 0})
        with clipper_app.app.app_context():
            repaired = db.session.get(Article, stale_id)
            preserved = db.session.get(Article, published_id)
            self.assertIsNone(repaired.tiktok_publish_status)
            self.assertIsNone(repaired.pending_publish_request)
            self.assertEqual(
                PlatformPost.query.filter_by(article_id=stale_id).one().status,
                'CANCELLED',
            )
            self.assertEqual(preserved.tiktok_publish_status, 'PUBLISH_COMPLETE')
            self.assertEqual(preserved.tiktok_publish_id, 'published-remote-id')
            published_post = PlatformPost.query.filter_by(
                article_id=published_id,
            ).one()
            self.assertEqual(published_post.status, 'PUBLISHED')
            self.assertEqual(published_post.external_id, 'published-remote-id')

    def test_discord_video_delivery_has_no_publish_approval_action(self):
        import discord_bot

        video_path = os.path.join(TEST_DIR, 'discord-output.mp4')
        with open(video_path, 'wb') as video:
            video.write(b'fake-video')
        with clipper_app.app.app_context():
            article = Article(
                url='https://example.test/discord-output',
                title='Discord output story',
                content='Body',
                tldr='A short summary.',
                status='video_done',
                video_path='discord-output.mp4',
            )
            db.session.add(article)
            db.session.commit()
            article_id = article.id

            message = SimpleNamespace(id=321)
            channel = SimpleNamespace(send=AsyncMock(return_value=message))
            with patch.object(discord_bot.discord, 'File', return_value=object()):
                asyncio.run(
                    discord_bot._post_video(
                        channel,
                        article,
                        video_file=discord_bot.Path(video_path),
                    )
                )

            db.session.expire_all()
            saved = db.session.get(Article, article_id)
            self.assertIsNone(saved.tiktok_publish_status)
            self.assertIsNone(saved.tiktok_approval_message_id)
            self.assertIsNone(saved.tiktok_approval_requested_at)
            sent_caption = channel.send.await_args.kwargs['content']
            self.assertNotIn('approval', sent_caption.lower())
            self.assertFalse(hasattr(discord_bot, 'on_raw_reaction_add'))

    def test_embedded_discord_bot_defers_daily_discovery_to_flask(self):
        import discord_bot

        with patch.object(discord_bot, 'DISCOVERY_ENABLED', True), patch.object(
            discord_bot,
            'DISCOVERY_SCHEDULER_MANAGED_EXTERNALLY',
            True,
        ):
            self.assertFalse(discord_bot._discord_discovery_scheduler_enabled())

    def test_video_route_assigns_a_unique_worker_ownership_token(self):
        with clipper_app.app.app_context():
            article = Article(
                url='https://example.test/video-token',
                title='Token story',
                content='Body',
                video_script='A complete narration.',
                status='summarized',
            )
            db.session.add(article)
            db.session.commit()
            article_id = article.id

        with patch.object(clipper_app, 'Thread') as thread_type:
            response = self.client.post(f'/api/articles/{article_id}/video', json={})

        self.assertEqual(response.status_code, 202)
        worker_args = thread_type.call_args.kwargs['args']
        generation_token = worker_args[-1]
        self.assertTrue(generation_token)
        with clipper_app.app.app_context():
            saved = db.session.get(Article, article_id)
            self.assertEqual(saved.status, 'generating_video')
            self.assertEqual(saved.video_generation_token, generation_token)

    def test_stale_video_worker_cannot_overwrite_a_newer_retry(self):
        handle, stale_output = tempfile.mkstemp(suffix='.mp4', dir=TEST_DIR)
        os.close(handle)
        with clipper_app.app.app_context():
            article = Article(
                url='https://example.test/stale-video-worker',
                title='Retry story',
                content='Body',
                video_script='A complete narration.',
                status='generating_video',
                video_generation_token='old-render-token',
            )
            db.session.add(article)
            db.session.commit()
            article_id = article.id

        def finish_after_retry(**_kwargs):
            Article.query.filter_by(id=article_id).update(
                {
                    Article.status: 'generating_video',
                    Article.video_generation_token: 'new-render-token',
                },
                synchronize_session=False,
            )
            db.session.commit()
            return stale_output

        with patch.object(
            clipper_app,
            'generate_video',
            side_effect=finish_after_retry,
        ):
            clipper_app.run_video_in_background(
                clipper_app.app.app_context(),
                article_id,
                generation_token='old-render-token',
            )

        self.assertFalse(os.path.exists(stale_output))
        with clipper_app.app.app_context():
            saved = db.session.get(Article, article_id)
            self.assertEqual(saved.status, 'generating_video')
            self.assertEqual(saved.video_generation_token, 'new-render-token')
            self.assertIsNone(saved.video_path)

    def test_article_list_eager_loads_metrics_in_one_query(self):
        from sqlalchemy import event

        with clipper_app.app.app_context():
            for index in range(3):
                article = Article(
                    url=f'https://example.test/metrics-list-{index}',
                    title=f'Metrics story {index}',
                    content='Body',
                )
                db.session.add(article)
                db.session.flush()
                db.session.add(VideoMetrics(
                    article_id=article.id,
                    tiktok_video_id=f'list-video-{index}',
                ))
            db.session.commit()

            metric_queries = []

            def capture_metric_query(_conn, _cursor, statement, *_args):
                if 'FROM video_metrics' in statement:
                    metric_queries.append(statement)

            event.listen(db.engine, 'before_cursor_execute', capture_metric_query)
            try:
                response = self.client.get('/api/articles')
            finally:
                event.remove(db.engine, 'before_cursor_execute', capture_metric_query)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(metric_queries), 1)


if __name__ == '__main__':
    unittest.main()
