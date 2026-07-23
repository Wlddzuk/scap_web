"""Facebook Page Reels publisher using Meta's start/upload/finish flow."""

from __future__ import annotations

import os
from typing import Any, BinaryIO
from urllib.parse import urlparse

import requests

from .base import PublishResult, Publisher, PublisherError, request_with_retries, response_json


GRAPH_ROOT = 'https://graph.facebook.com'
DEFAULT_GRAPH_VERSION = 'v23.0'
UPLOAD_TIMEOUT = (10, 180)
FAILED_PHASE_STATUSES = {'error', 'expired', 'failed'}
PUBLISHED_VIDEO_STATUSES = {'published', 'ready'}


class FacebookPublisher(Publisher):
    """Publish one local video as a Reel on a Facebook Page."""

    platform = 'facebook'

    def __init__(
        self,
        *,
        access_token: str,
        page_id: str,
        graph_version: str = DEFAULT_GRAPH_VERSION,
        session: requests.Session | None = None,
    ) -> None:
        self._access_token = access_token.strip()
        self._page_id = page_id.strip()
        self._graph_version = graph_version.strip() or DEFAULT_GRAPH_VERSION
        self._session = session or requests.Session()

    @property
    def _reels_url(self) -> str:
        return f'{GRAPH_ROOT}/{self._graph_version}/{self._page_id}/video_reels'

    def publish(self, article: Any, video_path: str, options: dict[str, Any]) -> PublishResult:
        self._require_configuration()
        description = str(
            options.get('caption') or options.get('description') or ''
        ).strip()
        title = str(options.get('title') or getattr(article, 'title', '') or '').strip()
        file_size = self._video_file_size(video_path)

        start_response = request_with_retries(
            self._session.post,
            self._reels_url,
            data={
                'access_token': self._access_token,
                'upload_phase': 'start',
            },
        )
        start_payload = response_json(start_response, platform='Facebook')
        video_id = start_payload.get('video_id')
        upload_url = start_payload.get('upload_url')
        if not video_id or not upload_url:
            raise PublisherError(
                'Facebook did not create a Reel upload session',
                code='missing_upload_session',
            )
        if not _is_meta_upload_url(str(upload_url)):
            raise PublisherError(
                'Facebook returned an invalid upload destination',
                code='invalid_upload_url',
            )

        self._upload_file(str(upload_url), video_path, file_size)

        finish_data = {
            'access_token': self._access_token,
            'upload_phase': 'finish',
            'video_id': str(video_id),
            'video_state': 'PUBLISHED',
        }
        if description:
            finish_data['description'] = description
        if title:
            finish_data['title'] = title
        finish_response = request_with_retries(
            self._session.post,
            self._reels_url,
            data=finish_data,
        )
        finish_payload = response_json(finish_response, platform='Facebook')
        if finish_payload.get('success') is not True:
            raise PublisherError(
                'Facebook did not accept the Reel for publishing',
                code='finish_rejected',
            )

        return PublishResult(
            platform=self.platform,
            status='PROCESSING_UPLOAD',
            accepted=True,
            external_id=str(video_id),
        )

    def check_status(self, external_id: str) -> PublishResult:
        self._require_configuration()
        response = request_with_retries(
            self._session.get,
            f'{GRAPH_ROOT}/{self._graph_version}/{external_id}',
            params={
                'fields': 'status',
                'access_token': self._access_token,
            },
        )
        payload = response_json(response, platform='Facebook')
        status = payload.get('status')
        if not isinstance(status, dict):
            raise PublisherError(
                'Facebook returned incomplete Reel status information',
                code='invalid_status_response',
            )

        video_status = str(status.get('video_status') or '').lower()
        phase_statuses = {
            str((status.get(phase) or {}).get('status') or '').lower()
            for phase in ('uploading_phase', 'processing_phase', 'publishing_phase')
            if isinstance(status.get(phase), dict)
        }
        if video_status in FAILED_PHASE_STATUSES or phase_statuses & FAILED_PHASE_STATUSES:
            return PublishResult(
                platform=self.platform,
                status='FAILED',
                accepted=False,
                external_id=external_id,
                error='Facebook could not process this Reel',
            )

        publishing_phase = status.get('publishing_phase')
        publishing_status = (
            str(publishing_phase.get('status') or '').lower()
            if isinstance(publishing_phase, dict)
            else ''
        )
        published = publishing_status == 'complete' or video_status in PUBLISHED_VIDEO_STATUSES
        return PublishResult(
            platform=self.platform,
            status='PUBLISHED' if published else 'PROCESSING_UPLOAD',
            accepted=True,
            external_id=external_id,
            permalink=f'https://www.facebook.com/reel/{external_id}' if published else None,
        )

    def _upload_file(self, upload_url: str, video_path: str, file_size: int) -> None:
        try:
            with open(video_path, 'rb') as video:
                response = request_with_retries(
                    self._rewinding_upload(video),
                    upload_url,
                    headers={
                        'Authorization': f'OAuth {self._access_token}',
                        'offset': '0',
                        'file_size': str(file_size),
                        'Content-Type': 'application/octet-stream',
                    },
                    data=video,
                    retries=2,
                    timeout=UPLOAD_TIMEOUT,
                )
        except OSError as exc:
            raise PublisherError(
                'The finished video file is unavailable',
                code='video_unavailable',
            ) from exc
        payload = response_json(response, platform='Facebook')
        if payload.get('success') is not True:
            raise PublisherError(
                'Facebook did not accept the Reel video',
                code='upload_rejected',
            )

    def _rewinding_upload(self, video: BinaryIO) -> Any:
        def post(upload_url: str, **kwargs: Any) -> requests.Response:
            video.seek(0)
            return self._session.post(upload_url, **kwargs)

        return post

    def _require_configuration(self) -> None:
        if not self._access_token or not self._page_id:
            raise PublisherError(
                'Facebook Page publishing is not connected',
                code='not_connected',
                status_code=400,
            )

    @staticmethod
    def _video_file_size(video_path: str) -> int:
        try:
            file_size = os.path.getsize(video_path)
        except OSError as exc:
            raise PublisherError(
                'The finished video file is unavailable',
                code='video_unavailable',
            ) from exc
        if file_size <= 0:
            raise PublisherError(
                'The finished video file is empty',
                code='video_empty',
            )
        return file_size


def _is_meta_upload_url(upload_url: str) -> bool:
    parsed = urlparse(upload_url)
    return parsed.scheme == 'https' and parsed.hostname == 'rupload.facebook.com'
