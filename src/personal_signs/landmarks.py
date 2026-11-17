"""MediaPipe detectors and per-frame features.

Every video frame becomes one feature vector of 278 numbers:

    [0:99]     33 body pose points (x, y, z)
    [99:162]   21 left-hand points (x, y, z)
    [162:225]  21 right-hand points (x, y, z)
    [225:277]  52 face-expression scores (blendshapes)
    [277]      1.0 when a face was found, else 0.0

Missing body parts stay 0, which the distance function treats as "not seen".
"""

from __future__ import annotations

import logging
import os
import urllib.request
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

POSE_POINTS = 33
HAND_POINTS = 21
LANDMARK_VALUE_COUNT = (POSE_POINTS + 2 * HAND_POINTS) * 3  # 225
FACE_BLENDSHAPE_COUNT = 52
FACE_PRESENT_INDEX = LANDMARK_VALUE_COUNT + FACE_BLENDSHAPE_COUNT  # 277
FEATURE_COUNT = FACE_PRESENT_INDEX + 1  # 278
LEFT_HAND_OFFSET = POSE_POINTS * 3  # 99
RIGHT_HAND_OFFSET = LEFT_HAND_OFFSET + HAND_POINTS * 3  # 162

POSE_MODEL = "pose_landmarker_full.task"
HAND_MODEL = "hand_landmarker.task"
FACE_MODEL = "face_landmarker.task"
MODEL_URLS = {
    POSE_MODEL: "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_full/float16/latest/pose_landmarker_full.task",
    HAND_MODEL: "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task",
    FACE_MODEL: "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/latest/face_landmarker.task",
}

_REPO_ROOT = Path(__file__).resolve().parents[2]


def models_dir() -> Path:
    """Folder with the MediaPipe .task files (PERSONAL_SIGNS_MODELS_DIR or ./models)."""
    return Path(os.environ.get("PERSONAL_SIGNS_MODELS_DIR") or _REPO_ROOT / "models")


def download_models(folder: Path | None = None) -> list[Path]:
    """Download any missing MediaPipe model files. Returns the files now present."""
    folder = folder or models_dir()
    folder.mkdir(parents=True, exist_ok=True)
    present = []
    for name, url in MODEL_URLS.items():
        target = folder / name
        if not target.is_file():
            partial = target.with_suffix(".part")
            logger.info("Downloading %s", name)
            urllib.request.urlretrieve(url, partial)  # noqa: S310 - fixed https URLs
            partial.replace(target)
        present.append(target)
    return present


def _mediapipe():
    try:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError("MediaPipe is not installed. Run: pip install mediapipe") from exc
    return mp, mp_python, mp_vision


def build_detectors() -> tuple:
    """Create (pose, hand, face) detectors. A missing model file gives None for that part."""
    _, mp_python, mp_vision = _mediapipe()
    folder = models_dir()

    def model(name: str) -> str | None:
        path = folder / name
        return str(path) if path.is_file() else None

    missing = [name for name in MODEL_URLS if model(name) is None]
    if missing:
        logger.warning(
            "MediaPipe model files missing in %s: %s. Run: python -m personal_signs download-models",
            folder,
            ", ".join(missing),
        )

    created = []
    try:
        pose = hand = face = None
        if path := model(POSE_MODEL):
            pose = mp_vision.PoseLandmarker.create_from_options(
                mp_vision.PoseLandmarkerOptions(base_options=mp_python.BaseOptions(model_asset_path=path))
            )
            created.append(pose)
        if path := model(HAND_MODEL):
            hand = mp_vision.HandLandmarker.create_from_options(
                mp_vision.HandLandmarkerOptions(
                    base_options=mp_python.BaseOptions(model_asset_path=path),
                    num_hands=2,
                )
            )
            created.append(hand)
        if path := model(FACE_MODEL):
            face = mp_vision.FaceLandmarker.create_from_options(
                mp_vision.FaceLandmarkerOptions(
                    base_options=mp_python.BaseOptions(model_asset_path=path),
                    num_faces=1,
                    output_face_blendshapes=True,
                )
            )
            created.append(face)
        return pose, hand, face
    except Exception:
        for detector in created:
            try:
                detector.close()
            except Exception:  # pragma: no cover - best effort cleanup
                logger.exception("Could not close a MediaPipe detector")
        raise


def frame_features(frame_bgr: np.ndarray, pose_detector, hand_detector, face_detector) -> np.ndarray:
    """Pose, both hands and face-expression features for one BGR video frame."""
    mp, _, _ = _mediapipe()
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    values = np.zeros(FEATURE_COUNT, dtype=np.float32)
    pose_landmarks = None

    if pose_detector is not None:
        pose = pose_detector.detect(image)
        if pose.pose_landmarks:
            pose_landmarks = pose.pose_landmarks[0]
            for index, point in enumerate(pose_landmarks[:POSE_POINTS]):
                values[index * 3:index * 3 + 3] = (point.x, point.y, point.z)

    if hand_detector is not None:
        hands = hand_detector.detect(image)
        for hand_index, points in enumerate(hands.hand_landmarks[:2] if hands.hand_landmarks else []):
            # Put each hand in its left/right slot when handedness is known.
            slot = hand_index
            if hands.handedness and hand_index < len(hands.handedness):
                slot = 0 if hands.handedness[hand_index][0].category_name.lower() == "left" else 1
            offset = LEFT_HAND_OFFSET if slot == 0 else RIGHT_HAND_OFFSET
            for index, point in enumerate(points[:HAND_POINTS]):
                start = offset + index * 3
                values[start:start + 3] = (point.x, point.y, point.z)

    if face_detector is not None:
        face = face_detector.detect(image)
        if not face.face_blendshapes:
            # In full-body framing the face is often too small to read. Crop
            # around the head points the pose model found, enlarge, and retry;
            # expression scores do not depend on where the crop is.
            crop = _head_crop(rgb, pose_landmarks)
            if crop.size:
                enlarged = cv2.resize(crop, (256, 256), interpolation=cv2.INTER_CUBIC)
                face = face_detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=enlarged))
        if face.face_blendshapes:
            for index, category in enumerate(face.face_blendshapes[0][:FACE_BLENDSHAPE_COUNT]):
                values[LANDMARK_VALUE_COUNT + index] = category.score
            values[FACE_PRESENT_INDEX] = 1.0
    return values


def _head_crop(rgb: np.ndarray, pose_landmarks) -> np.ndarray:
    height, width = rgb.shape[:2]
    if not pose_landmarks or len(pose_landmarks) <= 8:
        return rgb[: max(1, int(height * 0.45)), :, :]
    head = [pose_landmarks[index] for index in (0, 2, 5, 7, 8)]  # nose, eyes, ears
    xs = [point.x * width for point in head]
    ys = [point.y * height for point in head]
    center_x = pose_landmarks[0].x * width
    center_y = pose_landmarks[0].y * height + 0.04 * height
    span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
    box_width = max(0.30 * width, 2.5 * span_x, 1.7 * span_y, 96)
    box_height = max(1.50 * box_width, 2.5 * span_y, 160)
    x1, x2 = max(0, int(center_x - box_width / 2)), min(width, int(center_x + box_width / 2))
    y1, y2 = max(0, int(center_y - box_height / 2)), min(height, int(center_y + box_height / 2))
    return rgb[y1:y2, x1:x2]
