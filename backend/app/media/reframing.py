"""Face-centered 9:16 reframing for the video pipeline.

Adapted (and simplified) from the reference ``media/reframing.py``. It samples a
few frames, runs MediaPipe face detection (preferred) with OpenCV DNN and Haar
cascade fallback, and computes a weighted crop centre biased slightly upward for
better face framing. When no faces are found -- or when no detector is
available -- it degrades cleanly to a **center crop**. Everything is CPU-only.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from .common import logger
from .ffmpeg import (
    build_audio_output_args,
    build_final_video_encode_args,
    ffprobe_has_audio,
    ffprobe_video_size,
    round_to_even,
    run_ffmpeg_command,
)

MODELS_DIR = Path(__file__).resolve().parents[2] / "models"

try:  # opencv is required for face detection; degrade gracefully if missing
    import cv2

    _CV2_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    cv2 = None
    _CV2_AVAILABLE = False

try:
    import numpy as np

    _NUMPY_AVAILABLE = True
except Exception:  # pragma: no cover
    np = None
    _NUMPY_AVAILABLE = False


def compute_vertical_crop_dims(
    width: int, height: int, target_ratio: float = 9 / 16
) -> Tuple[int, int]:
    """Even-dimensioned 9:16 crop box that fits inside a source frame."""
    if width <= 0 or height <= 0:
        return width, height
    if width / height > target_ratio:
        crop_w = round_to_even(int(height * target_ratio))
        crop_h = round_to_even(height)
    else:
        crop_w = round_to_even(width)
        crop_h = round_to_even(int(width / target_ratio))
    return (
        max(2, min(crop_w, round_to_even(width))),
        max(2, min(crop_h, round_to_even(height))),
    )


def _center_crop_offsets(
    original_width: int, original_height: int, new_width: int, new_height: int
) -> Tuple[int, int]:
    x_offset = (
        round_to_even((original_width - new_width) // 2)
        if original_width > new_width
        else 0
    )
    y_offset = (
        round_to_even((original_height - new_height) // 2)
        if original_height > new_height
        else 0
    )
    return x_offset, y_offset


def _open_face_detectors():
    """Initialise MediaPipe, OpenCV DNN, and Haar in fallback order."""
    mp_face = None
    try:
        import mediapipe as mp

        if hasattr(mp, "solutions"):
            mp_face = mp.solutions.face_detection.FaceDetection(
                model_selection=1, min_detection_confidence=0.5
            )
        else:
            mp_face = mp.tasks.vision.FaceDetector.create_from_options(
                mp.tasks.vision.FaceDetectorOptions(
                    base_options=mp.tasks.BaseOptions(model_asset_path=str(MODELS_DIR / "blaze_face_short_range.tflite")),
                    min_detection_confidence=0.5,
                )
            )
    except Exception as exc:  # ImportError or runtime init failure
        logger.info("MediaPipe unavailable (%s); trying OpenCV detectors", exc)

    dnn = None
    haar = None
    if _CV2_AVAILABLE:
        try:
            yunet = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
            prototxt = MODELS_DIR / "deploy.prototxt"
            weights = MODELS_DIR / "res10_300x300_ssd_iter_140000.caffemodel"
            if yunet.is_file() and hasattr(cv2, "FaceDetectorYN"):
                dnn = cv2.FaceDetectorYN.create(str(yunet), "", (320, 320), 0.5)
            elif prototxt.is_file() and weights.is_file() and hasattr(cv2.dnn, "readNetFromCaffe"):
                dnn = cv2.dnn.readNetFromCaffe(str(prototxt), str(weights))
        except Exception as exc:
            logger.info("OpenCV DNN unavailable (%s); trying Haar", exc)
        try:
            haar = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
        except Exception as exc:  # pragma: no cover
            logger.info("Haar cascade unavailable (%s)", exc)
    return mp_face, dnn, haar


def _mediapipe_faces(detector, frame_bgr):
    height, width = frame_bgr.shape[:2]
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    faces = []
    if hasattr(detector, "process"):
        results = detector.process(rgb)
        for detection in results.detections or []:
            box = detection.location_data.relative_bounding_box
            fw, fh = box.width * width, box.height * height
            if fw > 20 and fh > 20:
                faces.append((int((box.xmin + box.width / 2) * width), int((box.ymin + box.height / 2) * height), int(fw * fh), float(detection.score[0])))
    else:
        import mediapipe as mp

        results = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
        for detection in results.detections or []:
            box = detection.bounding_box
            if box.width > 20 and box.height > 20:
                faces.append((box.origin_x + box.width // 2, box.origin_y + box.height // 2, box.width * box.height, float(detection.categories[0].score)))
    return faces


def _dnn_faces(detector, frame_bgr):
    height, width = frame_bgr.shape[:2]
    if hasattr(detector, "setInputSize"):
        detector.setInputSize((width, height))
        _, detections = detector.detect(frame_bgr)
        if detections is None:
            return []
        return [
            (int(left + fw / 2), int(top + fh / 2), int(fw * fh), float(row[-1]))
            for row in detections
            for left, top, fw, fh in [row[:4]]
            if fw > 20 and fh > 20
        ]
    detector.setInput(cv2.dnn.blobFromImage(frame_bgr, 1.0, (300, 300), (104, 177, 123)))
    results = detector.forward()
    faces = []
    for detection in results[0, 0]:
        confidence = float(detection[2])
        if confidence < 0.5:
            continue
        left, top, right, bottom = detection[3:7] * np.array([width, height, width, height])
        left, right = max(0, int(left)), min(width, int(right))
        top, bottom = max(0, int(top)), min(height, int(bottom))
        fw, fh = right - left, bottom - top
        if fw > 20 and fh > 20:
            faces.append((left + fw // 2, top + fh // 2, fw * fh, confidence))
    return faces


def detect_faces_in_clip(
    video_path: Path, start_time: float, end_time: float
) -> List[Tuple[int, int, int, float]]:
    """Sample frames and return detected (center_x, center_y, area, confidence).

    Returns an empty list (triggering a center-crop fallback) when neither
    detector is available or nothing is found.
    """
    if not (_CV2_AVAILABLE and _NUMPY_AVAILABLE):
        logger.info("OpenCV/NumPy unavailable; skipping face detection")
        return []

    mp_face, dnn, haar = _open_face_detectors()
    if mp_face is None and dnn is None and haar is None:
        return []

    face_centers: List[Tuple[int, int, int, float]] = []
    duration = max(0.0, end_time - start_time)
    sample_interval = min(0.5, duration / 10) if duration > 0 else 0.5
    sample_times: List[float] = []
    current = start_time
    while current < end_time:
        sample_times.append(current)
        current += max(0.1, sample_interval)
    if not sample_times:
        sample_times = [start_time]

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        logger.warning("Unable to open video for face detection: %s", video_path)
        return []

    try:
        for sample_time in sample_times:
            capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, sample_time) * 1000.0)
            ok, frame_bgr = capture.read()
            if not ok or frame_bgr is None:
                continue
            height, width = frame_bgr.shape[:2]
            frame_area = float(max(1, width * height))
            detected: List[Tuple[int, int, int, float]] = []

            if mp_face is not None:
                try:
                    detected = _mediapipe_faces(mp_face, frame_bgr)
                except Exception as exc:
                    logger.warning("MediaPipe detection failed: %s", exc)

            if not detected and dnn is not None:
                try:
                    detected = _dnn_faces(dnn, frame_bgr)
                except Exception as exc:
                    logger.warning("OpenCV DNN detection failed: %s", exc)

            if not detected and haar is not None:
                try:
                    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
                    faces = haar.detectMultiScale(
                        gray,
                        scaleFactor=1.1,
                        minNeighbors=3,
                        minSize=(40, 40),
                        maxSize=(int(width * 0.7), int(height * 0.7)),
                    )
                    for (fx, fy, fw, fh) in faces:
                        relative_size = (fw * fh) / frame_area
                        conf = min(0.9, 0.3 + relative_size * 2)
                        detected.append(
                            (fx + fw // 2, fy + fh // 2, int(fw * fh), conf)
                        )
                except Exception as exc:
                    logger.warning("Haar detection failed: %s", exc)

            for cx, cy, area, conf in detected:
                relative_area = area / frame_area
                if 0.005 < relative_area < 0.35:
                    face_centers.append((cx, cy, area, conf))
    finally:
        capture.release()
        if mp_face is not None:
            try:
                mp_face.close()
            except Exception:  # pragma: no cover
                pass

    logger.info("Detected %d face centers", len(face_centers))
    return face_centers


def detect_optimal_crop_region(
    video_path: Path,
    start_time: float,
    end_time: float,
    target_ratio: float = 9 / 16,
) -> Tuple[int, int, int, int]:
    """Return (x_offset, y_offset, crop_w, crop_h) for a 9:16 crop.

    Uses a face-weighted crop centre when faces are detected, otherwise falls
    back to a centred crop.
    """
    original_width, original_height = ffprobe_video_size(video_path)
    new_width, new_height = compute_vertical_crop_dims(
        original_width, original_height, target_ratio
    )

    try:
        face_centers = detect_faces_in_clip(video_path, start_time, end_time)
    except Exception as exc:
        logger.warning("Face detection error (%s); using center crop", exc)
        face_centers = []

    if face_centers:
        total_weight = sum(area * conf for _, _, area, conf in face_centers)
        if total_weight > 0:
            weighted_x = (
                sum(x * area * conf for x, _, area, conf in face_centers) / total_weight
            )
            weighted_y = (
                sum(y * area * conf for _, y, area, conf in face_centers) / total_weight
            )
            # Slight upward bias for better face framing.
            weighted_y = max(0, weighted_y - new_height * 0.1)
            x_offset = max(
                0, min(int(weighted_x - new_width // 2), original_width - new_width)
            )
            y_offset = max(
                0, min(int(weighted_y - new_height // 2), original_height - new_height)
            )
            logger.info("Face-centered crop from %d faces", len(face_centers))
            return (
                round_to_even(x_offset),
                round_to_even(y_offset),
                new_width,
                new_height,
            )

    logger.info("Using center crop (no faces detected)")
    x_offset, y_offset = _center_crop_offsets(
        original_width, original_height, new_width, new_height
    )
    return (x_offset, y_offset, new_width, new_height)


def reframe_to_vertical(
    input_path: Path,
    output_path: Path,
    start_time: float = 0.0,
    end_time: Optional[float] = None,
    target_width: int = 1080,
    target_height: int = 1920,
) -> bool:
    """Crop a clip to a face-centered 9:16 region and scale to 1080x1920."""
    try:
        original_width, original_height = ffprobe_video_size(input_path)
    except Exception as exc:
        logger.error("Cannot read source size for reframing: %s", exc)
        return False

    if end_time is None:
        end_time = start_time + 30.0

    x_offset, y_offset, crop_w, crop_h = detect_optimal_crop_region(
        input_path, start_time, end_time
    )
    has_audio = ffprobe_has_audio(input_path)
    video_filter = (
        f"crop={crop_w}:{crop_h}:{x_offset}:{y_offset},"
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
        f"crop={target_width}:{target_height},setsar=1"
    )
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        video_filter,
        *build_final_video_encode_args(),
        *build_audio_output_args(has_audio),
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    return run_ffmpeg_command(command, timeout=1800).returncode == 0
