# Face detector assets

The renderer tries MediaPipe, OpenCV DNN, then OpenCV's bundled Haar cascade.
Run `uv run python scripts/download_face_models.py` from `backend/` to download
the MediaPipe and DNN assets from Google's and OpenCV's official repositories.
Model files stay local and are ignored by Git. Haar and center cropping still
work if the assets are unavailable.
