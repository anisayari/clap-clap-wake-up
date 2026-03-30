import unittest

from clap_wake.window_layout import WindowBounds, plan_launch_layout


class WindowLayoutTests(unittest.TestCase):
    def test_plan_launch_layout_spreads_across_multiple_displays(self) -> None:
        displays = [
            WindowBounds(left=0, top=0, width=1200, height=800),
            WindowBounds(left=1200, top=0, width=1200, height=800),
        ]

        slots = plan_launch_layout(3, displays=displays)

        self.assertEqual(len(slots), 3)
        self.assertLess(slots[0].left, 1200)
        self.assertGreaterEqual(slots[2].left, 1200)

    def test_plan_launch_layout_splits_one_display_when_needed(self) -> None:
        displays = [WindowBounds(left=0, top=0, width=1200, height=800)]

        slots = plan_launch_layout(4, displays=displays)

        self.assertEqual(len(slots), 4)
        self.assertNotEqual(slots[0].left, slots[1].left)
        self.assertNotEqual(slots[0].top, slots[2].top)


if __name__ == "__main__":
    unittest.main()
