"""Personal Indian Sign Language (ISL) sign recognition.

Users teach the recognizer their own way of signing with a few short clips.
Each clip becomes a sequence of body, hand and face-expression features
(MediaPipe), and new clips are matched to the user's own examples with
Dynamic Time Warping (DTW). A reliability gate keeps bad examples out.
"""

__version__ = "0.1.0"
