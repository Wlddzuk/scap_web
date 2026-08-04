import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from flask import Flask

from models import Article, VideoMetrics, db
import performance_metrics
from performance_metrics import (
    ensure_metrics_scheduler,
    record_public_post_id,
    refresh_video_metrics,
)


class PerformanceMetricsTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix='clipper-metrics-')
        self.app = Flask(__name__, instance_path=self.test_dir)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{os.path.join(self.test_dir, 'test.db')}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

    def tearDown(self):
        performance_metrics._release_scheduler()
        db.session.remove()
        db.drop_all()
        self.context.pop()
        shutil.rmtree(self.test_dir)

    def _published_article(self):
        article = Article(
            url='https://example.test/space',
            title='A New View of Space',
            content='Body',
            tiktok_publish_status='PUBLISH_COMPLETE',
            tiktok_published_at=datetime.now(timezone.utc),
        )
        db.session.add(article)
        db.session.commit()
        return article

    def test_publish_status_records_documented_public_post_id(self):
        article = self._published_article()

        metrics = record_public_post_id(
            article,
            {'publicaly_available_post_id': [123456789]},
        )
        db.session.commit()

        self.assertEqual(metrics.tiktok_video_id, '123456789')
        self.assertIsNone(metrics.fetched_at)
        self.assertEqual(VideoMetrics.query.one().article_id, article.id)

    def test_deleting_article_cascades_to_metrics(self):
        article = self._published_article()
        db.session.add(VideoMetrics(article_id=article.id, tiktok_video_id='cascade-1'))
        db.session.commit()

        db.session.delete(article)
        db.session.commit()

        self.assertEqual(Article.query.count(), 0)
        self.assertEqual(VideoMetrics.query.count(), 0)

    @patch('performance_metrics.query_user_stats', return_value={'follower_count': 20})
    @patch('performance_metrics._fetch_recent_videos')
    def test_refresh_matches_caption_and_updates_latest_snapshot(self, fetch, _stats):
        article = self._published_article()
        fetch.return_value = [{
            'id': 'video-42',
            'title': 'A New View of Space',
            'create_time': int(datetime.now(timezone.utc).timestamp()),
            'view_count': 1200,
            'like_count': 80,
            'comment_count': 12,
            'share_count': 7,
        }]

        result = refresh_video_metrics(
            'token',
            caption_builder=lambda item: item.title,
        )

        self.assertEqual(result['updated'], 1)
        metrics = VideoMetrics.query.filter_by(article_id=article.id).one()
        self.assertEqual(metrics.tiktok_video_id, 'video-42')
        self.assertEqual(metrics.views, 1200)
        self.assertEqual(metrics.likes, 80)
        self.assertIsNone(metrics.watch_time)
        self.assertIsNotNone(metrics.fetched_at)

    @patch('performance_metrics.query_user_stats', return_value={})
    @patch('performance_metrics._fetch_recent_videos')
    def test_refresh_updates_row_that_was_only_mapped_by_publish_status(self, fetch, _stats):
        article = self._published_article()
        record_public_post_id(article, {'publicaly_available_post_id': ['direct-id']})
        db.session.commit()
        fetch.return_value = [{
            'id': 'direct-id',
            'view_count': 44,
            'like_count': 5,
            'comment_count': 2,
            'share_count': 1,
        }]

        result = refresh_video_metrics('token')

        metrics = VideoMetrics.query.filter_by(article_id=article.id).one()
        self.assertEqual(result['updated'], 1)
        self.assertEqual(metrics.views, 44)
        self.assertIsNotNone(metrics.fetched_at)

    def test_story_feedback_excludes_unfetched_and_recent_rows_and_normalizes_age(self):
        import story_finder

        now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)

        def add_performance(
            title,
            *,
            age_days,
            views,
            fetched=True,
            fetched_after_hours=None,
        ):
            published_at = now - timedelta(days=age_days)
            article = Article(
                url=f'https://example.test/{title.lower().replace(" ", "-")}',
                title=title,
                content='Body',
                site_name='Test source',
                tiktok_publish_status='PUBLISH_COMPLETE',
                tiktok_published_at=published_at,
            )
            db.session.add(article)
            db.session.flush()
            db.session.add(VideoMetrics(
                article_id=article.id,
                tiktok_video_id=f'video-{article.id}',
                views=views,
                likes=max(1, views // 20),
                fetched_at=(
                    published_at + timedelta(hours=fetched_after_hours)
                    if fetched_after_hours is not None
                    else now if fetched else None
                ),
            ))

        # The younger mature post wins on views/day despite fewer lifetime views.
        add_performance('Older raw leader', age_days=10, views=5000)
        add_performance('Faster mature post', age_days=2, views=2000)
        add_performance('Too recent', age_days=0.25, views=10000)
        add_performance('Mapped only', age_days=4, views=0, fetched=False)
        add_performance(
            'Premature snapshot',
            age_days=4,
            views=9000,
            fetched_after_hours=6,
        )
        db.session.commit()

        with patch.object(story_finder, 'app', self.app):
            feedback = story_finder._performance_examples(
                now=now,
                min_age_hours=24,
            )

        self.assertEqual(
            [item['title'] for item in feedback['top_performers']],
            ['Faster mature post'],
        )
        self.assertEqual(
            [item['title'] for item in feedback['bottom_performers']],
            ['Older raw leader'],
        )
        self.assertEqual(feedback['top_performers'][0]['views_per_day'], 1000.0)

    @patch.dict(
        os.environ,
        {
            'TIKTOK_METRICS_ENABLED': 'true',
            'TIKTOK_METRICS_INTERVAL_HOURS': '4',
        },
    )
    def test_metrics_scheduler_starts_once_with_interval_job(self):
        self.app.config['TESTING'] = False

        self.assertTrue(ensure_metrics_scheduler(self.app, lambda: None))
        self.assertTrue(ensure_metrics_scheduler(self.app, lambda: None))

        scheduler = performance_metrics._scheduler
        self.assertIsNotNone(scheduler)
        job = scheduler.get_job('clipper-tiktok-metrics')
        self.assertIsNotNone(job)
        self.assertIn('4:00:00', str(job.trigger))


if __name__ == '__main__':
    unittest.main()
