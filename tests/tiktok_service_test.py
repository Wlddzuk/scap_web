import json
import os
import tempfile
import unittest
from unittest.mock import patch

import tiktok_service


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


class TikTokServiceTests(unittest.TestCase):
    def test_token_cipher_round_trip_and_wrong_key(self):
        encrypted = tiktok_service.TokenCipher('secret-one').encrypt('access-token')
        self.assertNotIn('access-token', encrypted)
        self.assertEqual(
            tiktok_service.TokenCipher('secret-one').decrypt(encrypted),
            'access-token',
        )
        with self.assertRaises(ValueError):
            tiktok_service.TokenCipher('secret-two').decrypt(encrypted)

    def test_upload_plan_uses_one_request_for_small_generated_video(self):
        plan = tiktok_service.make_upload_plan(2_400_000)
        self.assertEqual(plan.chunk_size, 2_400_000)
        self.assertEqual(plan.total_chunk_count, 1)

    def test_upload_plan_splits_video_over_64_mb(self):
        plan = tiktok_service.make_upload_plan(70_000_123)
        self.assertEqual(plan.chunk_size, 32_000_000)
        self.assertEqual(plan.total_chunk_count, 2)

    @patch('tiktok_service.requests.post')
    def test_initialize_direct_post_uses_required_tiktok_shape(self, post):
        post.return_value = FakeResponse({
            'data': {'publish_id': 'pub-1', 'upload_url': 'https://upload.example/video'},
            'error': {'code': 'ok', 'message': '', 'log_id': 'log-1'},
        })
        plan = tiktok_service.make_upload_plan(2_400_000)
        result = tiktok_service.initialize_video_post(
            access_token='token',
            title='A science story #science',
            privacy_level='SELF_ONLY',
            disable_comment=True,
            disable_duet=True,
            disable_stitch=True,
            brand_content_toggle=False,
            brand_organic_toggle=False,
            upload_plan=plan,
        )

        self.assertEqual(result['publish_id'], 'pub-1')
        request_json = post.call_args.kwargs['json']
        self.assertEqual(request_json['post_info']['privacy_level'], 'SELF_ONLY')
        self.assertEqual(request_json['source_info'], {
            'source': 'FILE_UPLOAD',
            'video_size': 2_400_000,
            'chunk_size': 2_400_000,
            'total_chunk_count': 1,
        })

    @patch('tiktok_service.requests.put')
    def test_upload_uses_sequential_content_ranges(self, put):
        put.return_value = FakeResponse(status_code=201)
        handle, path = tempfile.mkstemp(suffix='.mp4')
        try:
            with os.fdopen(handle, 'wb') as video:
                video.write(b'0123456789A')
            plan = tiktok_service.UploadPlan(video_size=11, chunk_size=5, total_chunk_count=2)
            tiktok_service.upload_video_file('https://upload.example/video', path, plan)
        finally:
            os.unlink(path)

        self.assertEqual(put.call_count, 2)
        first = put.call_args_list[0].kwargs
        second = put.call_args_list[1].kwargs
        self.assertEqual(first['headers']['Content-Range'], 'bytes 0-4/11')
        self.assertEqual(first['data'], b'01234')
        self.assertEqual(second['headers']['Content-Range'], 'bytes 5-10/11')
        self.assertEqual(second['data'], b'56789A')


if __name__ == '__main__':
    unittest.main()
