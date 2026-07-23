"""YouTube Shorts publisher using Data API v3 resumable uploads."""

from __future__ import annotations

import json
import mimetypes
import os
import re
from typing import Any

import requests

from .base import PublishResult, Publisher, PublisherError, request_with_retries, response_json


UPLOAD_URL = 'https://www.googleapis.com/upload/youtube/v3/videos'
STATUS_URL = 'https://www.googleapis.com/youtube/v3/videos'
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024


def _article_tags(article: Any) -> list[str]:
    raw = getattr(article, 'hashtags', None)
    if not raw:
        return []
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(values, list):
        return []
    return [str(value).strip().lstrip('#') for value in values if str(value).strip()]


class YouTubePublisher(Publisher):
    platform = 'youtube'

    def __init__(self, *, access_token: str, session: requests.Session | None = None) -> None:
        self._access_token = access_token
        self._session = session or requests.Session()

    @property
    def _headers(self) -> dict[str, str]:
        return {'Authorization': f'Bearer {self._access_token}'}

    def publish(self, article: Any, video_path: str, options: dict[str, Any]) -> PublishResult:
        title = str(options.get('title') or getattr(article, 'title', '')).strip()
        description = str(options.get('description') or options.get('caption') or '').strip()
        if not title:
            raise PublisherError('Enter a YouTube title', code='title_required', status_code=400)
        if len(title) > 100:
            raise PublisherError(
                'YouTube title must be 100 characters or fewer',
                code='title_too_long',
                status_code=400,
            )
        if len(description) > 5_000:
            raise PublisherError(
                'YouTube description must be 5,000 characters or fewer',
                code='description_too_long',
                status_code=400,
            )
        privacy_status = str(options.get('privacy_status') or 'private').lower()
        if privacy_status not in {'private', 'unlisted', 'public'}:
            raise PublisherError(
                'Choose a valid YouTube privacy setting',
                code='invalid_privacy',
                status_code=400,
            )

        total_size = os.path.getsize(video_path)
        content_type = mimetypes.guess_type(video_path)[0] or 'video/mp4'
        metadata = {
            'snippet': {
                'title': title,
                'description': description,
                'tags': _article_tags(article),
                'categoryId': str(options.get('category_id') or '28'),
            },
            'status': {
                'privacyStatus': privacy_status,
                'selfDeclaredMadeForKids': bool(options.get('made_for_kids', False)),
            },
        }
        headers = {
            **self._headers,
            'Content-Type': 'application/json; charset=UTF-8',
            'X-Upload-Content-Length': str(total_size),
            'X-Upload-Content-Type': content_type,
        }
        response = request_with_retries(
            self._session.post,
            UPLOAD_URL,
            params={'uploadType': 'resumable', 'part': 'snippet,status'},
            headers=headers,
            json=metadata,
        )
        if not response.ok or not response.headers.get('Location'):
            raise PublisherError(
                'YouTube could not start the upload',
                code='upload_session_failed',
                status_code=response.status_code,
            )
        upload_url = response.headers['Location']
        payload = self._upload_chunks(upload_url, video_path, total_size, content_type)
        video_id = payload.get('id')
        if not video_id:
            raise PublisherError(
                'YouTube did not return a video id',
                code='missing_video_id',
            )
        return PublishResult(
            platform=self.platform,
            status='PUBLISHED',
            accepted=True,
            external_id=str(video_id),
            permalink=f'https://www.youtube.com/shorts/{video_id}',
        )

    def _upload_chunks(
        self,
        upload_url: str,
        video_path: str,
        total_size: int,
        content_type: str,
    ) -> dict[str, Any]:
        offset = 0
        with open(video_path, 'rb') as video:
            while offset < total_size:
                video.seek(offset)
                chunk = video.read(min(UPLOAD_CHUNK_BYTES, total_size - offset))
                end = offset + len(chunk) - 1
                response = request_with_retries(
                    self._session.put,
                    upload_url,
                    headers={
                        **self._headers,
                        'Content-Length': str(len(chunk)),
                        'Content-Type': content_type,
                        'Content-Range': f'bytes {offset}-{end}/{total_size}',
                    },
                    data=chunk,
                    retries=2,
                    timeout=(10, 180),
                )
                if response.status_code == 308:
                    remote_end = _uploaded_range_end(response.headers.get('Range'))
                    offset = remote_end + 1 if remote_end is not None else end + 1
                    continue
                return response_json(response, platform='YouTube')
        raise PublisherError('YouTube upload did not finish', code='upload_incomplete')

    def check_status(self, external_id: str) -> PublishResult:
        response = request_with_retries(
            self._session.get,
            STATUS_URL,
            params={'part': 'status', 'id': external_id},
            headers=self._headers,
        )
        payload = response_json(response, platform='YouTube')
        items = payload.get('items') or []
        if not items:
            return PublishResult(
                platform=self.platform,
                status='FAILED',
                accepted=False,
                external_id=external_id,
                error='YouTube could not find this upload',
            )
        upload_status = ((items[0].get('status') or {}).get('uploadStatus') or '').lower()
        failed = upload_status in {'failed', 'rejected', 'deleted'}
        return PublishResult(
            platform=self.platform,
            status='FAILED' if failed else 'PUBLISHED',
            accepted=not failed,
            external_id=external_id,
            permalink=f'https://www.youtube.com/shorts/{external_id}',
            error='YouTube could not publish this video' if failed else None,
        )


def _uploaded_range_end(value: str | None) -> int | None:
    match = re.search(r'(\d+)-(\d+)$', value or '')
    return int(match.group(2)) if match else None
