import unittest

from clap_wake.window_layout import (
    WindowBounds,
    VisibleWindow,
    build_saved_layout,
    match_windows_to_expected_slots,
    plan_launch_layout,
    restore_saved_layout,
    select_launch_layout,
)


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

    def test_saved_layout_restores_slots_on_same_display_topology(self) -> None:
        displays = [
            WindowBounds(left=0, top=0, width=1200, height=800),
            WindowBounds(left=1200, top=0, width=1200, height=800),
        ]
        original = [
            WindowBounds(left=40, top=50, width=500, height=400),
            WindowBounds(left=1320, top=60, width=600, height=500),
        ]

        saved = build_saved_layout(original, displays=displays)
        restored = restore_saved_layout(saved, count=2, displays=displays)

        self.assertEqual(restored, original)

    def test_select_launch_layout_prefers_saved_slots(self) -> None:
        displays = [WindowBounds(left=0, top=0, width=1200, height=800)]
        saved = build_saved_layout(
            [WindowBounds(left=55, top=66, width=700, height=500)],
            displays=displays,
        )

        slots = select_launch_layout(1, saved_layout=saved, displays=displays)

        self.assertEqual(slots, [WindowBounds(left=55, top=66, width=700, height=500)])

    def test_match_windows_to_expected_slots_prefers_owner_hints(self) -> None:
        expected = [
            WindowBounds(left=20, top=20, width=500, height=400),
            WindowBounds(left=620, top=20, width=500, height=400),
        ]
        windows = [
            VisibleWindow(owner_name="Google Chrome", bounds=WindowBounds(left=610, top=25, width=500, height=400)),
            VisibleWindow(owner_name="Codex", bounds=WindowBounds(left=25, top=25, width=500, height=400)),
        ]

        matched = match_windows_to_expected_slots(
            windows,
            expected,
            owner_hints=[["Codex"], ["Google Chrome"]],
        )

        self.assertEqual(matched[0].left, 25)
        self.assertEqual(matched[1].left, 610)


if __name__ == "__main__":
    unittest.main()
