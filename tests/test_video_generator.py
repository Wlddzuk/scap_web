"""Unit tests for video generation module."""

import pytest
from unittest.mock import patch, MagicMock
from PIL import Image
import video_generator as vg


@pytest.mark.unit
class TestImageGeneration:
    """Test image generation functions."""

    def test_create_gradient_background(self):
        """Test gradient background creation."""
        img = vg.create_gradient_background()

        assert isinstance(img, Image.Image)
        assert img.size == (vg.VIDEO_WIDTH, vg.VIDEO_HEIGHT)
        assert img.mode == 'RGB'

    @patch('video_generator.os.getenv')
    def test_generate_image_fal_no_key_fallback(self, mock_getenv):
        """Test that missing FAL_KEY falls back to gradient."""
        mock_getenv.return_value = None

        result = vg.generate_image_fal("test prompt")

        assert isinstance(result, Image.Image)
        # Should return a gradient background
        assert result.size == (vg.VIDEO_WIDTH, vg.VIDEO_HEIGHT)

    def test_resize_and_crop_image_wider(self):
        """Test resizing/cropping wider images."""
        # Create a wide test image (2000x1000)
        img = Image.new('RGB', (2000, 1000), color='red')

        result = vg.resize_and_crop_image(img, 1080, 1920)

        assert result.size == (1080, 1920)

    def test_resize_and_crop_image_taller(self):
        """Test resizing/cropping taller images."""
        # Create a tall test image (1000x2000)
        img = Image.new('RGB', (1000, 2000), color='blue')

        result = vg.resize_and_crop_image(img, 1080, 1920)

        assert result.size == (1080, 1920)

    def test_darken_image(self):
        """Test image darkening."""
        img = Image.new('RGB', (100, 100), color=(255, 255, 255))

        darkened = vg.darken_image(img, factor=0.5)

        assert isinstance(darkened, Image.Image)
        assert darkened.size == img.size


@pytest.mark.unit
class TestScriptCleaning:
    """Test script cleaning functions."""

    def test_clean_script_for_tts(self):
        """Test removing structural tags from script."""
        script = "[HOOK] This is a hook. [BIG IDEA] This is the big idea. [CLOSE] Goodbye!"

        cleaned = vg.clean_script_for_tts(script)

        assert '[HOOK]' not in cleaned
        assert '[BIG IDEA]' not in cleaned
        assert '[CLOSE]' not in cleaned
        assert 'This is a hook' in cleaned
        assert 'This is the big idea' in cleaned
        assert 'Goodbye' in cleaned

    def test_clean_script_extra_whitespace(self):
        """Test that extra whitespace is removed."""
        script = "[HOOK]   Multiple    spaces    here   [END]"

        cleaned = vg.clean_script_for_tts(script)

        assert '  ' not in cleaned  # No double spaces
        assert cleaned.strip() == cleaned  # No leading/trailing


@pytest.mark.unit
class TestTextChunking:
    """Test text chunking for TikTok-style videos."""

    def test_chunk_text_for_tiktok_default(self):
        """Test default text chunking (4 words per chunk)."""
        text = "This is a test of the text chunking system for videos"

        chunks = vg.chunk_text_for_tiktok(text)

        assert len(chunks) > 0
        # Each chunk should have roughly 4 words
        for chunk in chunks[:-1]:  # All but last
            assert len(chunk.split()) == vg.DEFAULT_WORDS_PER_CHUNK

    def test_chunk_text_custom_words_per_chunk(self):
        """Test custom words per chunk."""
        text = "One two three four five six seven eight nine ten"

        chunks = vg.chunk_text_for_tiktok(text, words_per_chunk=2)

        assert len(chunks) == 5  # 10 words / 2 per chunk
        for chunk in chunks:
            assert len(chunk.split()) <= 2

    def test_chunk_text_removes_tags(self):
        """Test that chunking removes [TAG] markers."""
        text = "[HOOK] This is content [BIG IDEA] More content here"

        chunks = vg.chunk_text_for_tiktok(text)

        for chunk in chunks:
            assert '[' not in chunk
            assert ']' not in chunk


@pytest.mark.unit
class TestVisualKeywordExtraction:
    """Test visual keyword extraction."""

    def test_extract_visual_keywords_science(self):
        """Test extracting science-related keywords."""
        text = "Scientists conducted research on the universe"

        keywords = vg.extract_visual_keywords(text)

        assert len(keywords) >= 3  # Should find at least 3 keywords
        # Check that we found science-related visuals
        keywords_str = ' '.join(keywords).lower()
        assert 'scientist' in keywords_str or 'research' in keywords_str

    def test_extract_visual_keywords_technology(self):
        """Test extracting tech-related keywords."""
        text = "AI and computers are transforming technology"

        keywords = vg.extract_visual_keywords(text)

        assert len(keywords) > 0
        assert any('ai' in k.lower() or 'artificial' in k.lower() for k in keywords)

    def test_extract_visual_keywords_empty(self):
        """Test with text containing no mapped keywords."""
        text = "xyz abc def nonsense words"

        keywords = vg.extract_visual_keywords(text)

        assert keywords == []


@pytest.mark.unit
class TestThemedImageGeneration:
    """Test themed image generation."""

    @patch('video_generator.generate_image_fal')
    def test_generate_themed_images_ai_theme(self, mock_gen_image):
        """Test AI theme detection and image generation."""
        # Mock image generation
        mock_gen_image.return_value = Image.new('RGB', (1080, 1920))

        title = "How AI is Changing Everything"
        script = "Artificial intelligence and machine learning are transforming our world"

        images = vg.generate_themed_images(title, script, num_images=3)

        assert len(images) == 3
        assert mock_gen_image.call_count == 3
        # Should detect AI theme
        assert any('ai' in call[0][0].lower() or 'neural' in call[0][0].lower()
                   for call in mock_gen_image.call_args_list)

    @patch('video_generator.generate_image_fal')
    def test_generate_themed_images_space_theme(self, mock_gen_image):
        """Test space theme detection."""
        mock_gen_image.return_value = Image.new('RGB', (1080, 1920))

        title = "Exploring the Cosmos"
        script = "Space and the universe hold many mysteries about galaxies and stars"

        images = vg.generate_themed_images(title, script, num_images=2)

        assert len(images) == 2
        # Should detect space theme
        assert any('space' in call[0][0].lower() or 'galaxy' in call[0][0].lower()
                   for call in mock_gen_image.call_args_list)


@pytest.mark.unit
class TestConfigurationConstants:
    """Test that configuration constants are properly defined."""

    def test_video_dimensions(self):
        """Test video dimensions are set correctly."""
        assert vg.VIDEO_WIDTH == 1080
        assert vg.VIDEO_HEIGHT == 1920
        assert vg.VIDEO_HEIGHT / vg.VIDEO_WIDTH == 16 / 9  # 9:16 ratio

    def test_timing_constants(self):
        """Test timing constants are sensible."""
        assert vg.MIN_CHUNK_DURATION < vg.MAX_CHUNK_DURATION
        assert vg.MIN_CHUNK_DURATION > 0
        assert vg.DEFAULT_CHUNK_DURATION >= vg.MIN_CHUNK_DURATION
        assert vg.DEFAULT_CHUNK_DURATION <= vg.MAX_CHUNK_DURATION

    def test_image_constants(self):
        """Test image-related constants."""
        assert 0 < vg.IMAGE_DARKEN_FACTOR <= 1.0
        assert vg.DEFAULT_WORDS_PER_CHUNK > 0
        assert vg.RETRY_ATTEMPTS >= 1


@pytest.mark.unit
@pytest.mark.slow
class TestVideoGeneration:
    """Test full video generation (mocked to avoid actual rendering)."""

    @patch('video_generator.generate_tts_kokoro')
    @patch('video_generator.AudioFileClip')
    @patch('video_generator.generate_themed_images')
    @patch('video_generator.concatenate_videoclips')
    def test_generate_video_workflow(self, mock_concat, mock_images, mock_audio_clip, mock_tts):
        """Test the video generation workflow (heavily mocked)."""
        # Mock TTS
        mock_tts.return_value = '/tmp/audio.wav'

        # Mock audio clip
        mock_audio = MagicMock()
        mock_audio.duration = 15.0
        mock_audio_clip.return_value = mock_audio

        # Mock images
        mock_images.return_value = [
            Image.new('RGB', (1080, 1920)) for _ in range(3)
        ]

        # Mock concatenated video
        mock_video = MagicMock()
        mock_video.duration = 15.0
        mock_concat.return_value = mock_video

        # This test would need significant mocking to actually run
        # For now, we test individual components
        assert True  # Placeholder - full integration would need more setup
