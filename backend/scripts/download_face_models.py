"""Install optional face-detector data files from Google and OpenCV."""

from pathlib import Path
from urllib.request import urlopen

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
MODELS = {
    "face_detection_yunet_2023mar.onnx": "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "blaze_face_short_range.tflite": "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite",
    "deploy.prototxt": "https://raw.githubusercontent.com/opencv/opencv/4.x/samples/dnn/face_detector/deploy.prototxt",
    "res10_300x300_ssd_iter_140000.caffemodel": "https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000.caffemodel",
}


def main():
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in MODELS.items():
        destination = MODELS_DIR / name
        if destination.is_file() and destination.stat().st_size:
            print(f"Already installed: {name}")
            continue
        with urlopen(url, timeout=30) as response:
            data = response.read()
        if not data:
            raise RuntimeError(f"Empty download: {name}")
        destination.write_bytes(data)
        print(f"Installed: {name}")


if __name__ == "__main__":
    main()
