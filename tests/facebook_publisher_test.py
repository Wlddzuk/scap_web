"""Focused tests for the Facebook Page Reels publishing adapter."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from publishers import FacebookPublisher, PublisherError


def _response(payload, *, status=200):
    response = Mock()
    response.status_code = status
    response.ok = status < 400
    response.headers = {}
    response.json.return_value = payload
    return response


def test_publish_runs_start_local_upload_and_finish(tmp_path):
    video_path = tmp_path / 'short.mp4'
    video_path.write_bytes(b'facebook-video')
    session = Mock()
    session.post.side_effect = [
        _response({
            'video_id': 'reel-123',
            'upload_url': 'https://rupload.facebook.com/video-upload/v23.0/reel-123',
        }),
        _response({'success': True}),
        _response({'success': True}),
    ]
    publisher = FacebookPublisher(
        access_token='page-token',
        page_id='page-456',
        session=session,
    )

    result = publisher.publish(
        SimpleNamespace(title='Article title'),
        str(video_path),
        {'caption': 'Science in sixty seconds'},
    )

    assert result.platform == 'facebook'
    assert result.status == 'PROCESSING_UPLOAD'
    assert result.accepted is True
    assert result.external_id == 'reel-123'

    start_call, upload_call, finish_call = session.post.call_args_list
    assert start_call.args[0].endswith('/v23.0/page-456/video_reels')
    assert start_call.kwargs['data']['upload_phase'] == 'start'
    assert start_call.kwargs['timeout'] == (10, 60)

    assert upload_call.args[0].startswith('https://rupload.facebook.com/')
    assert upload_call.kwargs['headers'] == {
        'Authorization': 'OAuth page-token',
        'offset': '0',
        'file_size': '14',
        'Content-Type': 'application/octet-stream',
    }
    assert upload_call.kwargs['timeout'] == (10, 180)

    assert finish_call.kwargs['data'] == {
        'access_token': 'page-token',
        'upload_phase': 'finish',
        'video_id': 'reel-123',
        'video_state': 'PUBLISHED',
        'description': 'Science in sixty seconds',
        'title': 'Article title',
    }
    assert finish_call.kwargs['timeout'] == (10, 60)


def test_upload_retry_rewinds_the_video_body(tmp_path):
    video_path = tmp_path / 'short.mp4'
    video_path.write_bytes(b'retry-this-video')
    session = Mock()
    uploaded_bodies = []

    def post(url, **kwargs):
        if 'video_reels' in url and kwargs['data']['upload_phase'] == 'start':
            return _response({
                'video_id': 'reel-retry',
                'upload_url': 'https://rupload.facebook.com/video-upload/v23.0/reel-retry',
            })
        if url.startswith('https://rupload.facebook.com/'):
            uploaded_bodies.append(kwargs['data'].read())
            if len(uploaded_bodies) == 1:
                raise requests.Timeout('provider details must stay private')
            return _response({'success': True})
        return _response({'success': True})

    session.post.side_effect = post
    publisher = FacebookPublisher(
        access_token='page-token',
        page_id='page-456',
        session=session,
    )

    with patch('publishers.base.time.sleep'):
        result = publisher.publish(SimpleNamespace(title='Story'), str(video_path), {})

    assert result.external_id == 'reel-retry'
    assert uploaded_bodies == [b'retry-this-video', b'retry-this-video']


def test_check_status_returns_processing_published_and_failed_results():
    session = Mock()
    session.get.side_effect = [
        _response({
            'status': {
                'video_status': 'processing',
                'uploading_phase': {'status': 'complete'},
                'processing_phase': {'status': 'in_progress'},
                'publishing_phase': {'status': 'not_started'},
            },
        }),
        _response({
            'status': {
                'video_status': 'ready',
                'publishing_phase': {'status': 'complete'},
            },
        }),
        _response({
            'status': {
                'video_status': 'error',
                'processing_phase': {'status': 'failed'},
            },
        }),
    ]
    publisher = FacebookPublisher(
        access_token='page-token',
        page_id='page-456',
        session=session,
    )

    processing = publisher.check_status('reel-1')
    published = publisher.check_status('reel-2')
    failed = publisher.check_status('reel-3')

    assert processing.status == 'PROCESSING_UPLOAD'
    assert processing.permalink is None
    assert published.status == 'PUBLISHED'
    assert published.permalink == 'https://www.facebook.com/reel/reel-2'
    assert failed.status == 'FAILED'
    assert failed.accepted is False
    assert failed.error == 'Facebook could not process this Reel'
    assert all(call.kwargs['timeout'] == (10, 60) for call in session.get.call_args_list)


def test_publish_rejects_non_meta_upload_destination_without_contacting_it(tmp_path):
    video_path = tmp_path / 'short.mp4'
    video_path.write_bytes(b'video')
    session = Mock()
    session.post.return_value = _response({
        'video_id': 'reel-123',
        'upload_url': 'https://attacker.test/upload',
    })
    publisher = FacebookPublisher(
        access_token='page-token',
        page_id='page-456',
        session=session,
    )

    try:
        publisher.publish(SimpleNamespace(title='Story'), str(video_path), {})
    except PublisherError as exc:
        assert exc.code == 'invalid_upload_url'
        assert str(exc) == 'Facebook returned an invalid upload destination'
    else:
        raise AssertionError('PublisherError was not raised')

    assert session.post.call_count == 1


def test_publish_masks_provider_response_details(tmp_path):
    video_path = tmp_path / 'short.mp4'
    video_path.write_bytes(b'video')
    session = Mock()
    session.post.return_value = _response(
        {'error': {'message': 'secret provider diagnostic'}},
        status=400,
    )
    publisher = FacebookPublisher(
        access_token='page-token',
        page_id='page-456',
        session=session,
    )

    try:
        publisher.publish(SimpleNamespace(title='Story'), str(video_path), {})
    except PublisherError as exc:
        assert str(exc) == 'Facebook could not complete the request'
        assert 'secret provider diagnostic' not in str(exc)
    else:
        raise AssertionError('PublisherError was not raised')
