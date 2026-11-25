"""Sample 2: pick evenly spaced frames from a short video with OpenCV.

Run:  pip install opencv-python-headless numpy
      python sample_frames.py my_clip.mp4
"""

import sys

import cv2
import numpy as np


def sample_frames(path: str, count: int = 16) -> list:
    """Return `count` frames spread evenly from the start to the end of the clip."""
    cap = cv2.VideoCapture(path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total < 2:
        cap.release()
        raise ValueError("The video is too short or could not be read.")
    wanted = set(np.linspace(0, total - 1, count).astype(int).tolist())
    frames = []
    for index in range(total):
        ok, frame = cap.read()  # one pass through the clip is faster than seeking
        if not ok:
            break
        if index in wanted:
            frames.append(frame)
    cap.release()
    return frames


if __name__ == "__main__":
    picked = sample_frames(sys.argv[1])
    print(f"Picked {len(picked)} frames of size {picked[0].shape[1]}x{picked[0].shape[0]}")
