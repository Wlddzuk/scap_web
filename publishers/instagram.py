"""Instagram Reels pull-based container publishing adapter."""

from __future__ import annotations

from typing import Any

import requests

from .base import PublishResult, Publisher, PublisherError, request_with_retries, response_json


GRAPH_ROOT = 'https://graph.facebook.com'
DEFAULT_GRAPH_VERSION = 'v23.0'


class InstagramPublisher(Publisher):
    platform = 'instagram'

    def __init__(
        self,
        *,
        access_token: str,
        instagram_user_id: str,
        public_video_url: str,
        graph_version: str = DEFAULT_GRAPH_VERSION,
        session: requests.Session | None = None,
    ) -> None:
        self._access_token = access_token
        self._instagram_user_id = instagram_user_id
        self._public_video_url = public_video_url
        self._graph_version = graph_version
        self._session = session or requests.Session()

    @property
    def _base_url(self) -> str:
        return f'{GRAPH_ROOT}/{self._graph_version}'

    def publish(self, article: Any, video_path: str, options: dict[str, Any]) -> PublishResult:
        del article, video_path
        caption = str(options.get('caption') or '').strip()
        if len(caption) > 2_200:
            raise PublisherError(
                'Instagram caption must be 2,200 characters or fewer',
                code='caption_too_long',
                status_code=400,
            )
        response = request_with_retries(
            self._session.post,
            f'{self._base_url}/{self._instagram_user_id}/media',
            data={
                'media_type': 'REELS',
                'video_url': self._public_video_url,
                'caption': caption,
                'share_to_feed': 'true' if options.get('share_to_feed', True) else 'false',
                'access_token': self._access_token,
            },
        )
        payload = response_json(response, platform='Instagram')
        container_id = payload.get('id')
        if not container_id:
            raise PublisherError(
                'Instagram did not create a video container',
                code='missing_container_id',
            )
        return PublishResult(
            platform=self.platform,
            status='PROCESSING_CONTAINER',
            accepted=True,
            external_id=str(container_id),
        )

    def check_status(self, external_id: str) -> PublishResult:
        response = request_with_retries(
            self._session.get,
            f'{self._base_url}/{external_id}',
            params={
                'fields': 'status_code,status',
                'access_token': self._access_token,
            },
        )
        payload = response_json(response, platform='Instagram')
        status_code = str(payload.get('status_code') or '').upper()
        if status_code in {'ERROR', 'EXPIRED'}:
            return PublishResult(
                platform=self.platform,
                status='FAILED',
                accepted=False,
                external_id=external_id,
                error='Instagram could not process this video',
            )
        if status_code != 'FINISHED':
            return PublishResult(
                platform=self.platform,
                status='PROCESSING_CONTAINER',
                accepted=True,
                external_id=external_id,
            )

        publish_response = request_with_retries(
            self._session.post,
            f'{self._base_url}/{self._instagram_user_id}/media_publish',
            data={
                'creation_id': external_id,
                'access_token': self._access_token,
            },
        )
        publish_payload = response_json(publish_response, platform='Instagram')
        media_id = publish_payload.get('id')
        if not media_id:
            raise PublisherError(
                'Instagram did not return a published media id',
                code='missing_media_id',
            )
        permalink = self._fetch_permalink(str(media_id))
        return PublishResult(
            platform=self.platform,
            status='PUBLISHED',
            accepted=True,
            external_id=str(media_id),
            permalink=permalink,
        )

    def _fetch_permalink(self, media_id: str) -> str | None:
        response = request_with_retries(
            self._session.get,
            f'{self._base_url}/{media_id}',
            params={'fields': 'permalink', 'access_token': self._access_token},
            retries=1,
        )
        try:
            return response_json(response, platform='Instagram').get('permalink')
        except PublisherError:
            return None
