"""Word-synced burned-in captions (ASS) + hook-title overlay.

Adapted from the reference ``media/captions.py``. Builds animated word-synced
ASS subtitles from cached word timings, applies the selected caption template
(karaoke/pop/fade/bounce animation, highlight/emphasis colours, uppercase,
max_words_per_line, position_y), injects contextual emojis only when the runtime
can render colour emojis, and burns an optional AI-written hook title into the
top safe area for the opening seconds of each clip.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..caption_templates import get_template
from ..emoji_captions import POWER_WORDS, annotate_caption_words, normalize_token
from ..font_registry import FONTS_DIR, find_font_path, get_font_family_name
from .common import (
    EMOJI_FONT_NAME,
    HOOK_TITLE_MIN_SECONDS,
    HOOK_TITLE_SECONDS,
    HOOK_TITLE_TOP_MARGIN_FRAC,
    logger,
)
from .ffmpeg import run_ffmpeg_command, subtitles_filter_fragment
from .timeline import (
    get_words_for_keep_ranges,
    get_words_in_range,
    normalize_source_ranges,
)
from .transcription import load_cached_transcript_data

try:  # only used by the (optional) emoji-support probe
    import numpy as np
except Exception:  # pragma: no cover
    np = None

_EMOJI_SUPPORT_CACHE: Optional[bool] = None


def get_scaled_font_size(base_font_size: int, video_width: int) -> int:
    """Scale caption font size by output width while preserving user choices."""
    scaled_size = round(base_font_size * (video_width / 560.0))
    return max(26, min(132, scaled_size))


def get_safe_vertical_position(
    video_height: int, text_height: int, position_y: float
) -> int:
    """Return a subtitle y position clamped inside a top/bottom safe area."""
    min_top_padding = max(40, int(video_height * 0.05))
    min_bottom_padding = max(120, int(video_height * 0.10))
    desired_y = int(video_height * position_y - text_height // 2)
    max_y = video_height - min_bottom_padding - text_height
    return max(min_top_padding, min(desired_y, max_y))


def emoji_rendering_supported() -> bool:
    """Whether this environment's libass renders colour emojis (cached, one-shot)."""
    global _EMOJI_SUPPORT_CACHE
    if _EMOJI_SUPPORT_CACHE is not None:
        return _EMOJI_SUPPORT_CACHE
    if np is None:
        _EMOJI_SUPPORT_CACHE = False
        return False

    result = False
    try:
        with tempfile.TemporaryDirectory(prefix="viralclip_emojiprobe_") as probe_dir:
            root = Path(probe_dir)
            ass = root / "probe.ass"
            frame = root / "probe.png"
            ass.write_text(
                "[Script Info]\n"
                "ScriptType: v4.00+\nPlayResX: 120\nPlayResY: 120\n\n"
                "[V4+ Styles]\n"
                "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, "
                "BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, Encoding\n"
                f"Style: D,{EMOJI_FONT_NAME},90,&H00FFFFFF,&H00000000,&H00000000,0,1,0,0,5,1\n\n"
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
                "Effect, Text\n"
                "Dialogue: 0,0:00:00.00,0:00:01.00,D,,0,0,0,,"
                "{\\pos(60,60)}\U0001F525\n",
                encoding="utf-8",
            )
            fonts = FONTS_DIR if FONTS_DIR.exists() else None
            fragment = subtitles_filter_fragment(ass, fonts)
            command = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=120x120:d=1",
                "-vf", fragment,
                "-frames:v", "1",
                str(frame),
            ]
            if run_ffmpeg_command(command, timeout=60).returncode == 0 and frame.exists():
                from PIL import Image

                arr = np.asarray(Image.open(frame).convert("RGB"), dtype=np.int16)
                spread = arr.max(axis=2) - arr.min(axis=2)
                result = int((spread > 40).sum()) > 30
    except Exception as exc:
        logger.info("Emoji support probe failed (%s); disabling caption emojis", exc)
        result = False

    _EMOJI_SUPPORT_CACHE = result
    logger.info("Caption colour-emoji rendering supported: %s", result)
    return result


def ass_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds - (hours * 3600) - (minutes * 60)
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def hex_to_ass_color(
    value: Optional[str], fallback: str = "#FFFFFF", include_alpha: bool = True
) -> str:
    value = (value or fallback).strip()
    if value.startswith("#"):
        value = value[1:]
    alpha = 0
    if len(value) == 8:
        css_alpha = int(value[6:8], 16)
        alpha = 255 - css_alpha
        value = value[:6]
    if len(value) != 6:
        value = fallback.lstrip("#")
        if len(value) == 8:
            css_alpha = int(value[6:8], 16)
            alpha = 255 - css_alpha
            value = value[:6]
    red, green, blue = value[0:2], value[2:4], value[4:6]
    alpha_part = f"{alpha:02X}" if include_alpha else "00"
    return f"&H{alpha_part}{blue}{green}{red}&"


def escape_ass_text(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", " ")
        .strip()
    )


def ass_font_name(font_family: Optional[str]) -> str:
    if not font_family:
        return "Arial"
    font_path = find_font_path(font_family)
    if font_path:
        return get_font_family_name(Path(font_path)) or Path(font_path).stem
    return font_family or "Arial"


def _balance_title_lines(words: List[str], max_chars: int) -> List[str]:
    """Split title words into one line, or two balanced lines."""
    text = " ".join(words)
    if len(text) <= max_chars or len(words) < 2:
        return [text]
    best_lines = [text]
    best_longest = len(text)
    for i in range(1, len(words)):
        first = " ".join(words[:i])
        second = " ".join(words[i:])
        longest = max(len(first), len(second))
        if longest < best_longest:
            best_longest = longest
            best_lines = [first, second]
    return best_lines


def build_hook_title_ass(
    hook_title: str,
    template: Dict[str, Any],
    video_width: int,
    video_height: int,
    output_duration: float,
    font_name: str,
    caption_font_px: int,
) -> Tuple[str, List[str]]:
    """Build the (style_line, dialogue_events) for a burned-in hook title."""
    uppercase = bool(template.get("uppercase"))
    title_text = hook_title.upper() if uppercase else hook_title

    primary = hex_to_ass_color(template.get("font_color"), "#FFFFFF")
    highlight = hex_to_ass_color(
        template.get("emphasis_color") or template.get("highlight_color"), "#FFE000"
    )
    outline = hex_to_ass_color(template.get("stroke_color") or "#000000", "#000000")
    back_color = hex_to_ass_color(template.get("background_color"), "#00000080")

    base_px = max(34, min(66, int(caption_font_px * 0.82)))
    usable_width = video_width - 2 * max(
        48, int(video_width * HOOK_TITLE_TOP_MARGIN_FRAC)
    )
    max_chars = max(10, int(usable_width / (base_px * 0.52)))
    lines = _balance_title_lines(title_text.split(), max_chars)
    longest = max(len(line) for line in lines)
    hook_px = base_px
    if longest > max_chars:
        hook_px = max(30, min(base_px, int(usable_width / (longest * 0.52))))

    base_stroke = int(template.get("stroke_width", 3) or 0)
    has_outline = template.get("stroke_color") is not None and base_stroke > 0
    border_style = 3 if (not has_outline and template.get("background_color")) else 1
    outline_px = max(base_stroke, round(hook_px * base_stroke / 26)) if has_outline else 0
    if border_style == 3:
        outline_px = max(4, hook_px // 6)
    elif outline_px == 0:
        outline_px = max(2, hook_px // 16)
    shadow_px = max(2, hook_px // 20) if template.get("shadow") else 0
    margin_v = max(48, int(video_height * HOOK_TITLE_TOP_MARGIN_FRAC))

    style_line = (
        f"Style: Hook,{font_name},{hook_px},{primary},&H000000FF,{outline},{back_color},"
        f"1,0,0,0,100,100,0,0,{border_style},{outline_px},{shadow_px},8,60,60,{margin_v},1"
    )

    rendered_lines: List[str] = []
    for line in lines:
        spans: List[str] = []
        for word in line.split():
            token = normalize_token(word)
            accented = bool(token) and (
                token in POWER_WORDS or any(c.isdigit() for c in token)
            )
            color = highlight if accented else primary
            spans.append(f"{{\\c{color}}}{escape_ass_text(word)}")
        rendered_lines.append(" ".join(spans))
    text = "\\N".join(rendered_lines)

    start = 0.12
    end = min(HOOK_TITLE_SECONDS, max(HOOK_TITLE_MIN_SECONDS, output_duration - 0.25))
    if output_duration <= HOOK_TITLE_MIN_SECONDS:
        start, end = 0.0, max(0.5, output_duration)
    entrance = "\\fad(160,240)"
    if template.get("word_pop", True):
        entrance += "\\fscx90\\fscy90\\t(0,160,\\fscx100\\fscy100)"
    events = [
        f"Dialogue: 1,{ass_timestamp(start)},{ass_timestamp(end)},Hook,,0,0,0,,"
        f"{{{entrance}}}{text}"
    ]
    return style_line, events


def build_ass_subtitles(
    video_path: Path,
    clip_start: float,
    clip_end: float,
    video_width: int,
    video_height: int,
    output_ass_path: Path,
    font_family: Optional[str] = None,
    font_size: Optional[int] = None,
    font_color: Optional[str] = None,
    caption_template: str = "default",
    keep_ranges: Optional[List[Tuple[float, float]]] = None,
    hook_title: Optional[str] = None,
    include_captions: bool = True,
    caption_words: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """Generate animated word-synced ASS subtitles (+ optional hook title).

    When ``hook_title`` is set, the headline is burned into the top safe area
    even if word-synced captions are unavailable or disabled.
    """
    transcript_data = load_cached_transcript_data(video_path)
    template = get_template(caption_template)
    effective_font_family = font_family or template["font_family"]
    effective_font_size = int(font_size) if font_size else int(template["font_size"])
    effective_font_color = font_color or template["font_color"]
    animation = template.get("animation", "karaoke")

    relevant_words: List[Dict[str, Any]] = list(caption_words or [])
    if (
        include_captions
        and not relevant_words
        and transcript_data
        and transcript_data.get("words")
    ):
        if keep_ranges:
            relevant_words = get_words_for_keep_ranges(transcript_data, keep_ranges)
        else:
            relevant_words = get_words_in_range(transcript_data, clip_start, clip_end)
    if not relevant_words and not hook_title:
        logger.warning("No words or hook title available for ASS subtitles")
        return False

    uppercase = bool(template.get("uppercase"))
    enable_emoji = bool(template.get("emoji", True)) and emoji_rendering_supported()
    word_pop = bool(template.get("word_pop", True))
    word_box = bool(template.get("word_box"))
    glow = bool(template.get("glow"))
    has_outline = template.get("stroke_color") is not None
    enable_emphasis = animation != "none"

    primary = hex_to_ass_color(effective_font_color)
    highlight = hex_to_ass_color(template.get("highlight_color"), "#FFE000")
    emphasis_color = hex_to_ass_color(
        template.get("emphasis_color") or template.get("highlight_color"), "#FFE000"
    )
    outline = hex_to_ass_color(template.get("stroke_color") or "#000000", "#000000")
    back_color = hex_to_ass_color(template.get("background_color"), "#00000080")
    box_color = hex_to_ass_color(
        template.get("word_box_color") or template.get("highlight_color"), "#00BF49"
    )

    font_px = get_scaled_font_size(effective_font_size, video_width)
    base_stroke = int(template.get("stroke_width", 3) or 0)
    outline_px = (
        max(base_stroke, round(font_px * base_stroke / 26))
        if (has_outline and base_stroke)
        else 0
    )
    shadow_px = max(2, font_px // 20) if template.get("shadow") else 0
    box_bord = max(outline_px + 2, font_px // 5)
    pos_y = float(template.get("position_y", 0.80))
    est_text_height = int(font_px * 1.5)
    y_pos = get_safe_vertical_position(video_height, est_text_height, pos_y)
    font_name = ass_font_name(effective_font_family)
    border_style = (
        3 if template.get("background") and template.get("background_color") else 1
    )

    hook_style_block = ""
    hook_events: List[str] = []
    if hook_title:
        if keep_ranges:
            ranges = normalize_source_ranges(keep_ranges)
            output_duration = sum(end - start for start, end in ranges)
        else:
            output_duration = max(0.0, clip_end - clip_start)
        hook_style_line, hook_events = build_hook_title_ass(
            hook_title,
            template,
            video_width,
            video_height,
            output_duration,
            font_name,
            font_px,
        )
        hook_style_block = f"{hook_style_line}\n"

    emoji_by_idx, emphasis_idx = annotate_caption_words(
        relevant_words,
        None,
        enable_emoji=enable_emoji,
        enable_emphasis=enable_emphasis,
    )

    max_words = max(1, int(template.get("max_words_per_line", 4) or 4))
    chunk_size = max_words

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_px},{primary},&H000000FF,{outline},{back_color},1,0,0,0,100,100,0,0,{border_style},{outline_px},{shadow_px},5,60,60,60,1
{hook_style_block}
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    line_prefix = (
        f"{{\\pos({video_width // 2},{y_pos})" + ("\\blur4" if glow else "") + "}"
    )
    font_tag = f"\\fn{font_name}"

    def render_text(global_idx: int, word: Dict[str, Any]) -> str:
        text = str(word.get("text", ""))
        if uppercase:
            text = text.upper()
        disp = escape_ass_text(text)
        emoji = emoji_by_idx.get(global_idx)
        if emoji:
            disp = f"{disp} {{\\fn{EMOJI_FONT_NAME}}}{emoji}"
        return disp

    def active_span(disp: str) -> str:
        tags = f"{font_tag}\\c{highlight}"
        if word_box:
            tags += f"\\3c{box_color}\\bord{box_bord}\\shad0"
        return f"{{{tags}}}{disp}"

    def idle_span(global_idx: int, disp: str) -> str:
        color = (
            emphasis_color
            if (enable_emphasis and global_idx in emphasis_idx)
            else primary
        )
        tags = f"{font_tag}\\c{color}"
        if word_box:
            tags += f"\\3c{outline}\\bord{outline_px}\\shad{shadow_px}"
        return f"{{{tags}}}{disp}"

    line_entrance = "\\fscx92\\fscy92\\t(0,140,\\fscx100\\fscy100)" if word_pop else ""

    events: List[str] = []
    total = len(relevant_words)
    for chunk_start in range(0, total, chunk_size):
        chunk = relevant_words[chunk_start : chunk_start + chunk_size]
        indices = list(range(chunk_start, chunk_start + len(chunk)))
        chunk_end = float(chunk[-1]["end"])

        if animation == "karaoke":
            for local_i, word in enumerate(chunk):
                start = float(word["start"])
                end = (
                    float(chunk[local_i + 1]["start"])
                    if local_i + 1 < len(chunk)
                    else chunk_end
                )
                if end <= start:
                    end = start + 0.05
                parts = []
                for local_j, other in enumerate(chunk):
                    gj = indices[local_j]
                    disp = render_text(gj, other)
                    parts.append(
                        active_span(disp)
                        if local_j == local_i
                        else idle_span(gj, disp)
                    )
                line = " ".join(parts)
                entrance = (
                    f"{{{line_entrance}}}"
                    if (line_entrance and local_i == 0)
                    else ""
                )
                events.append(
                    f"Dialogue: 0,{ass_timestamp(start)},{ass_timestamp(end)},"
                    f"Default,,0,0,0,,{line_prefix}{entrance}{line}"
                )
        else:
            start = float(chunk[0]["start"])
            end = chunk_end
            if end <= start:
                end = start + 0.05
            spans = []
            for local_j, word in enumerate(chunk):
                gj = indices[local_j]
                disp = render_text(gj, word)
                color = (
                    emphasis_color
                    if (enable_emphasis and gj in emphasis_idx)
                    else primary
                )
                spans.append(f"{{{font_tag}\\c{color}}}{disp}")
            chunk_text = " ".join(spans)

            effect = ""
            if animation == "fade":
                effect = "{\\fad(120,120)}"
            elif animation == "pop":
                effect = (
                    "{\\fscx88\\fscy88\\t(0,130,\\fscx106\\fscy106)"
                    "\\t(130,250,\\fscx100\\fscy100)}"
                )
            elif animation == "bounce":
                effect = (
                    "{\\fscx70\\fscy70\\t(0,120,\\fscx112\\fscy112)"
                    "\\t(120,240,\\fscx100\\fscy100)}"
                )
            events.append(
                f"Dialogue: 0,{ass_timestamp(start)},{ass_timestamp(end)},"
                f"Default,,0,0,0,,{line_prefix}{effect}{chunk_text}"
            )

    all_events = hook_events + events
    output_ass_path.write_text(header + "\n".join(all_events) + "\n", encoding="utf-8")
    logger.info(
        "Wrote ASS subtitles: %s (%d events%s)",
        output_ass_path,
        len(all_events),
        ", hook title" if hook_events else "",
    )
    return True
