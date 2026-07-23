"""TikTok adapter over the existing, tested Direct Post client functions."""

from __future__ import annotations

import os
from typing import Any, Callable

from .base import PublishResult, Publisher, PublisherError


class TikTokPublisher(Publisher):
    platform = 'tiktok'

    def __init__(
        self,
        *,
        access_context: Callable[[], tuple[str, Any, dict[str, Any]]],
        duration_seconds: Callable[[str], float],
        make_upload_plan: Callable[[int], Any],
        initialize_video_post: Callable[..., dict[str, Any]],
        upload_video_file: Callable[[str, str, Any], None],
        fetch_publish_status: Callable[[str, str], dict[str, Any]],
        status_access_token: Callable[[], tuple[str, Any]],
        allow_public: bool = False,
    ) -> None:
        self._access_context = access_context
        self._duration_seconds = duration_seconds
        self._make_upload_plan = make_upload_plan
        self._initialize_video_post = initialize_video_post
        self._upload_video_file = upload_video_file
        self._fetch_publish_status = fetch_publish_status
        self._status_access_token = status_access_token
        self._allow_public = allow_public

    def publish(self, article: Any, video_path: str, options: dict[str, Any]) -> PublishResult:
        if options.get('consent') is not True:
            raise PublisherError(
                'TikTok music usage consent is required',
                code='consent_required',
                status_code=400,
            )

        title = str(options.get('title') or options.get('caption') or '').strip()
        if not title:
            raise PublisherError('Enter a TikTok caption', code='caption_required', status_code=400)
        if len(title) > 2_200:
            raise PublisherError(
                'TikTok caption must be 2,200 characters or fewer',
                code='caption_too_long',
                status_code=400,
            )

        privacy_level = str(options.get('privacy_level') or '').strip()
        if not privacy_level:
            raise PublisherError(
                'Select a TikTok privacy setting',
                code='privacy_required',
                status_code=400,
            )
        if not self._allow_public and privacy_level != 'SELF_ONLY':
            raise PublisherError(
                'This unaudited integration is locked to Only you (SELF_ONLY) posts',
                code='public_posting_disabled',
                status_code=400,
            )

        brand_content = options.get('brand_content_toggle') is True
        brand_organic = options.get('brand_organic_toggle') is True
        if brand_content and privacy_level == 'SELF_ONLY':
            raise PublisherError(
                'TikTok does not allow branded content posts with Only you privacy',
                code='invalid_branded_content_privacy',
                status_code=400,
            )

        access_token, _, creator = self._access_context()
        privacy_options = creator.get('privacy_level_options') or []
        if privacy_level not in privacy_options:
            raise PublisherError(
                'That privacy setting is not available for this TikTok account',
                code='privacy_level_option_mismatch',
                status_code=400,
            )

        duration = self._duration_seconds(video_path)
        max_duration = int(creator.get('max_video_post_duration_sec') or 0)
        if max_duration and duration > max_duration:
            raise PublisherError(
                f'This video is {duration:.1f}s; the connected account allows up to {max_duration}s',
                code='video_too_long',
                status_code=400,
            )

        allow_comment = options.get('allow_comment') is True and not creator.get('comment_disabled', False)
        allow_duet = options.get('allow_duet') is True and not creator.get('duet_disabled', False)
        allow_stitch = options.get('allow_stitch') is True and not creator.get('stitch_disabled', False)
        upload_plan = self._make_upload_plan(os.path.getsize(video_path))
        initialized = self._initialize_video_post(
            access_token=access_token,
            title=title,
            privacy_level=privacy_level,
            disable_comment=not allow_comment,
            disable_duet=not allow_duet,
            disable_stitch=not allow_stitch,
            brand_content_toggle=brand_content,
            brand_organic_toggle=brand_organic,
            upload_plan=upload_plan,
        )
        publish_id = initialized.get('publish_id')
        upload_url = initialized.get('upload_url')
        if not publish_id or not upload_url:
            raise PublisherError(
                'TikTok did not return an upload destination',
                code='missing_upload_url',
            )
        self._upload_video_file(upload_url, video_path, upload_plan)
        return PublishResult(
            platform=self.platform,
            status='PROCESSING_UPLOAD',
            accepted=True,
            external_id=str(publish_id),
        )

    def check_status(self, external_id: str) -> PublishResult:
        access_token, _ = self._status_access_token()
        status_data = self._fetch_publish_status(access_token, external_id)
        status = status_data.get('status') or 'UNKNOWN'
        failed = bool(status_data.get('fail_reason')) or status == 'FAILED'
        return PublishResult(
            platform=self.platform,
            status='FAILED' if failed else status,
            accepted=not failed,
            external_id=external_id,
            error='TikTok could not publish this video' if failed else None,
        )
