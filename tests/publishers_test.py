"""Unit tests for the platform-neutral publisher contract."""

from types import SimpleNamespace
from unittest.mock import Mock

from publishers import (
    InstagramPublisher,
    PublishResult,
    TikTokPublisher,
    YouTubePublisher,
)


def _response(payload, *, status=200, headers=None):
    response = Mock()
    response.status_code = status
    response.ok = status < 400
    response.headers = headers or {}
    response.json.return_value = payload
    return response


def test_tiktok_adapter_uses_common_contract_without_changing_client_shape(tmp_path):
    video_path = tmp_path / 'short.mp4'
    video_path.write_bytes(b'video')
    upload_plan = object()
    initialize = Mock(return_value={
        'publish_id': 'publish-1',
        'upload_url': 'https://upload.test/one',
    })
    upload = Mock()
    publisher = TikTokPublisher(
        access_context=lambda: (
            'access-token',
            object(),
            {
                'privacy_level_options': ['SELF_ONLY'],
                'max_video_post_duration_sec': 180,
            },
        ),
        duration_seconds=lambda _path: 42.0,
        make_upload_plan=lambda _size: upload_plan,
        initialize_video_post=initialize,
        upload_video_file=upload,
        fetch_publish_status=Mock(),
        status_access_token=Mock(),
    )

    result = publisher.publish(
        SimpleNamespace(title='Story'),
        str(video_path),
        {
            'title': 'Story #science',
            'privacy_level': 'SELF_ONLY',
            'consent': True,
        },
    )

    assert isinstance(result, PublishResult)
    assert result.status == 'PROCESSING_UPLOAD'
    assert result.external_id == 'publish-1'
    initialize.assert_called_once()
    upload.assert_called_once_with(
        'https://upload.test/one',
        str(video_path),
        upload_plan,
    )


def test_instagram_publish_returns_after_container_creation():
    session = Mock()
    session.post.return_value = _response({'id': 'container-1'})
    publisher = InstagramPublisher(
        access_token='meta-token',
        instagram_user_id='ig-user-1',
        public_video_url='https://clipper.test/public-media/short.mp4?sig=signed',
        session=session,
    )

    result = publisher.publish(
        SimpleNamespace(title='Story'),
        '/local/short.mp4',
        {'caption': 'A science caption'},
    )

    assert result.status == 'PROCESSING_CONTAINER'
    assert result.external_id == 'container-1'
    request_data = session.post.call_args.kwargs['data']
    assert request_data['media_type'] == 'REELS'
    assert request_data['video_url'].startswith('https://')


def test_youtube_uses_resumable_video_insert(tmp_path):
    video_path = tmp_path / 'short.mp4'
    video_path.write_bytes(b'video-bytes')
    session = Mock()
    session.post.return_value = _response(
        {},
        headers={'Location': 'https://upload.youtube.test/session-1'},
    )
    session.put.return_value = _response({'id': 'youtube-1'})
    publisher = YouTubePublisher(access_token='youtube-token', session=session)

    result = publisher.publish(
        SimpleNamespace(title='Short title', hashtags='["#science"]'),
        str(video_path),
        {'description': 'Description', 'privacy_status': 'private'},
    )

    assert result.status == 'PUBLISHED'
    assert result.permalink == 'https://www.youtube.com/shorts/youtube-1'
    assert session.post.call_args.kwargs['params']['uploadType'] == 'resumable'
    assert session.put.call_args.kwargs['headers']['Content-Range'].endswith('/11')
