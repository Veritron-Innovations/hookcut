import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from render_video import compute_smart_crop


class SmartCropTests(unittest.TestCase):
    def test_compute_smart_crop_uses_face_center_when_available(self):
        crop = compute_smart_crop(1920, 1080, 1080, 1920, face_center=(1400, 520))
        self.assertEqual(crop[2], 1080)
        self.assertEqual(crop[3], 1920)
        self.assertGreater(crop[0], 700)
        self.assertLess(crop[0], 1000)
        self.assertGreaterEqual(crop[1], 0)

    def test_compute_smart_crop_falls_back_to_center(self):
        crop = compute_smart_crop(1920, 1080, 1080, 1920)
        self.assertEqual(crop[2], 1080)
        self.assertEqual(crop[3], 1920)
        self.assertEqual(crop[0], 1166)
        self.assertEqual(crop[1], 0)


if __name__ == "__main__":
    unittest.main()
