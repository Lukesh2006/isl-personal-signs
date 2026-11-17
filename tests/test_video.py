"""Video decoding: evenly spaced frames from real MP4 and WebM files."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from personal_signs import engine


def _write_video(path: Path, codec: str, frames: int = 30) -> bool:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*codec), 15.0, (64, 48))
    if not writer.isOpened():
        return False
    for index in range(frames):
        # Frame N is filled with brightness 8*N so samples can be identified.
        writer.write(np.full((48, 64, 3), min(255, index * 8), dtype=np.uint8))
    writer.release()
    return path.stat().st_size > 0


class SampleFramesTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patcher = patch.object(engine, "SAMPLE_ROOT", self.root / "training_samples")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _check(self, video: Path):
        indices, frames = engine.sample_frames(video.read_bytes(), frame_count=8)
        self.assertEqual(len(indices), 8)
        self.assertEqual(indices[0], 0)
        self.assertGreaterEqual(len(frames), 6)
        brightness = [int(frames[i].mean()) for i in sorted(frames)]
        self.assertEqual(brightness, sorted(brightness), "frames should come out in time order")
        self.assertFalse(any((self.root / "training_samples" / ".processing").iterdir()), "temp clip left behind")

    def test_mp4(self):
        video = self.root / "clip.mp4"
        if not _write_video(video, "mp4v"):
            self.skipTest("this OpenCV build cannot write MP4")
        self._check(video)

    def test_webm(self):
        video = self.root / "clip.webm"
        if not _write_video(video, "VP80"):
            self.skipTest("this OpenCV build cannot write WebM")
        self._check(video)

    def test_unreadable_bytes_raise_value_error(self):
        with self.assertRaises(ValueError):
            engine.sample_frames(b"not a video")


if __name__ == "__main__":
    unittest.main()
