"""Motion-based aircraft detection using OpenCV background subtraction.

The strategy is deliberately simple — sky is a near-uniform background, so a
running background model (MOG2) makes any moving object pop. We then filter
the resulting foreground mask by blob size and shape to throw out:

  - tiny noise pixels (sensor + JPEG compression artifacts)
  - massive blobs (clouds, rolling sun glare, shadows)
  - ribbon-shaped blobs (power lines swaying, lens flare streaks)

Anything that survives is a candidate "moving thing in the sky" — the
incident manager decides what to do about it.
"""

from __future__ import annotations

import cv2
import numpy as np


class MotionDetector:
    def __init__(self, config):
        self.config = config
        self.bg = cv2.createBackgroundSubtractorMOG2(
            history=config.mog2_history,
            varThreshold=config.mog2_var_threshold,
            detectShadows=False,
        )
        # Pre-built kernels for morphology
        self._k_open = np.ones((3, 3), np.uint8)
        self._k_close = np.ones((5, 5), np.uint8)

    def detect(self, frame) -> tuple[list[tuple[int, int, int, int, float]], "cv2.Mat"]:
        """Run detection on one frame.

        Returns (blobs, mask) where each blob is (x, y, w, h, area).
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        k = self.config.detector_blur_kernel
        if k % 2 == 0:
            k += 1
        gray = cv2.GaussianBlur(gray, (k, k), 0)
        mask = self.bg.apply(gray)
        # Open removes specks; close fills small gaps inside a real target.
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._k_open)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._k_close)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        blobs: list[tuple[int, int, int, int, float]] = []
        for c in contours:
            area = float(cv2.contourArea(c))
            if not (self.config.min_blob_area <= area <= self.config.max_blob_area):
                continue
            x, y, w, h = cv2.boundingRect(c)
            if h == 0 or w == 0:
                continue
            if self.config.max_aspect_ratio > 0:
                ar = max(w / h, h / w)
                if ar > self.config.max_aspect_ratio:
                    continue
            blobs.append((x, y, w, h, area))
        return blobs, mask
