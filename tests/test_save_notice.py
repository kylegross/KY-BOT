import unittest
from types import SimpleNamespace

from ky_bot.views.save_notice import draft_notice, mark_saved, take_notice


class SaveNoticeTests(unittest.TestCase):
    def test_save_confirmation_is_shown_once(self):
        owner = SimpleNamespace()
        self.assertIsNone(take_notice(owner))
        mark_saved(owner)
        self.assertEqual(take_notice(owner), "Saved.")
        self.assertIsNone(take_notice(owner))

    def test_specific_confirmation_replaces_generic_success(self):
        owner = SimpleNamespace()
        mark_saved(owner)
        self.assertEqual(take_notice(owner, "Roles saved."), "Roles saved.")
        self.assertIsNone(take_notice(owner))

    def test_drafts_never_claim_persistence_until_success(self):
        owner = SimpleNamespace()
        self.assertIsNone(draft_notice(owner, {"text": "Before"}))
        self.assertIsNone(draft_notice(owner, {"text": "Before"}))
        self.assertTrue(draft_notice(owner, {"text": "After"}).startswith("Draft updated."))
        mark_saved(owner)
        self.assertEqual(draft_notice(owner, {"text": "After"}), "Saved.")
        self.assertIsNone(draft_notice(owner, {"text": "After"}))
