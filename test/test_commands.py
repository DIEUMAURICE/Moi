import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "telegram" not in sys.modules:
    telegram_stub = ModuleType("telegram")

    class InlineKeyboardButton:
        def __init__(self, text, callback_data=None):
            self.text = text
            self.callback_data = callback_data

    class InlineKeyboardMarkup:
        def __init__(self, inline_keyboard):
            self.inline_keyboard = inline_keyboard

    telegram_stub.InlineKeyboardButton = InlineKeyboardButton
    telegram_stub.InlineKeyboardMarkup = InlineKeyboardMarkup
    sys.modules["telegram"] = telegram_stub

from utils.helpers import (
    parse_amount, get_level, xp_for_level, xp_progress,
    roll_success, get_karma_multiplier, lifestyle_score,
)
from utils.pagination import paginate_lines, build_callback_data


class TestHelpers(unittest.TestCase):
    def test_parse_amount_supports_decimal_suffixes(self):
        self.assertEqual(parse_amount("1.5k"), 1500)
        self.assertEqual(parse_amount("2.25m"), 2_250_000)

    def test_parse_amount_supports_percent_and_keywords(self):
        self.assertEqual(parse_amount("50%", 2000), 1000)
        self.assertEqual(parse_amount("tout", 3500), 3500)
        self.assertEqual(parse_amount("moitié", 3500), 1750)

    def test_parse_amount_rejects_invalid_values(self):
        self.assertIsNone(parse_amount("abc"))
        self.assertIsNone(parse_amount("1..5k"))

    def test_level_helpers_are_consistent(self):
        self.assertEqual(get_level(0), 1)
        self.assertEqual(xp_for_level(1), 0)
        self.assertEqual(xp_for_level(2), 500)
        self.assertEqual(xp_for_level(3), 2000)
        lvl, xp_in, xp_next = xp_progress(2000)
        self.assertGreaterEqual(lvl, 1)
        self.assertGreaterEqual(xp_in, 0)
        self.assertGreater(xp_next, 0)

    def test_get_karma_multiplier_boundaries(self):
        self.assertEqual(get_karma_multiplier(500), 1.40)
        self.assertEqual(get_karma_multiplier(200), 1.25)
        self.assertEqual(get_karma_multiplier(0), 1.0)
        self.assertEqual(get_karma_multiplier(-200), 0.75)
        self.assertEqual(get_karma_multiplier(-500), 0.55)

    def test_lifestyle_score_accounts_for_stress(self):
        user = {
            "health": 80,
            "energy": 60,
            "hunger": 70,
            "happiness": 90,
            "stress": 40,
        }
        self.assertEqual(lifestyle_score(user), 61)

    def test_roll_success_respects_probability_clamp(self):
        with patch("utils.helpers.random.random", return_value=0.0):
            self.assertTrue(roll_success(0.5))
        with patch("utils.helpers.random.random", return_value=0.99):
            self.assertFalse(roll_success(0.5))
        with patch("utils.helpers.random.random", return_value=0.97):
            self.assertTrue(roll_success(0.95, skill_bonus=0.5))
        with patch("utils.helpers.random.random", return_value=0.98):
            self.assertFalse(roll_success(0.0, karma_bonus=-1.0))


class TestPagination(unittest.IsolatedAsyncioTestCase):
    async def test_paginate_lines_builds_navigation_markup(self):
        lines = [f"Ligne {i}" for i in range(1, 26)]
        text, markup = await paginate_lines(lines, page=2, per_page=10, header="Début", footer="\nFin")

        self.assertIn("Ligne 11", text)
        self.assertIn("Ligne 20", text)
        self.assertNotIn("Ligne 10", text)
        self.assertNotIn("Ligne 21", text)
        self.assertIsNotNone(markup)
        buttons = markup.inline_keyboard[0]
        self.assertEqual(buttons[0].callback_data, "page_1")
        self.assertEqual(buttons[1].text, "2/3")
        self.assertEqual(buttons[2].callback_data, "page_3")

    async def test_paginate_lines_clamps_page_range(self):
        lines = [f"Entrée {i}" for i in range(1, 6)]
        text, markup = await paginate_lines(lines, page=99, per_page=2)

        self.assertIn("Entrée 5", text)
        self.assertNotIn("Entrée 1", text)
        self.assertIsNotNone(markup)

    def test_build_callback_data_helper(self):
        self.assertEqual(build_callback_data(4), "page_4")


if __name__ == "__main__":
    unittest.main()
