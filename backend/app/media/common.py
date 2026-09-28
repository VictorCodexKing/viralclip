"""Shared media constants and the logger used across the media subpackage.

Adapted (and heavily trimmed) from the reference project's ``media/common.py``.
We keep only the rendering constants and word-timeline knobs the captions and
reframing code actually use in this local, no-Docker build.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger("viralclip.media")

# --- Fonts --------------------------------------------------------------------

FONTS_DIR = Path(__file__).parent.parent.parent / "fonts"
EMOJI_FONT_NAME = "Noto Color Emoji"

# --- Transcript cache ---------------------------------------------------------

TRANSCRIPT_CACHE_SCHEMA_VERSION = 2

# Analysis-line segmentation knobs (word grouping for AssemblyAI/word timings).
ANALYSIS_SEGMENT_MIN_WORDS = 8
ANALYSIS_SEGMENT_MAX_WORDS = 8
ANALYSIS_SEGMENT_MAX_DURATION_MS = 12_000
ANALYSIS_LONG_UTTERANCE_MAX_WORDS = 24
ANALYSIS_LONG_UTTERANCE_MAX_DURATION_MS = 20_000
ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_MS = 45_000
ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_WORDS = 80

# --- Encoding constants -------------------------------------------------------

FINAL_VIDEO_CRF = 19
FINAL_VIDEO_PRESET = "medium"
INTERMEDIATE_CRF = 18
OUTPUT_FPS = 30
AUDIO_BITRATE = "192k"
LOUDNORM_FILTER = "loudnorm=I=-14:TP=-1.5:LRA=11"

# --- Hook-title overlay -------------------------------------------------------

HOOK_TITLE_SECONDS = 4.0
HOOK_TITLE_MIN_SECONDS = 1.5
HOOK_TITLE_TOP_MARGIN_FRAC = 0.07

# --- Sentence extension -------------------------------------------------------

CLIP_END_SENTENCE_EXTENSION_SECONDS = 3.0
CLIP_END_PADDING_SECONDS = 0.35
SENTENCE_END_RE = re.compile(r"""[.!?]["')\]}]*$""")
