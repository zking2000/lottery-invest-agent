from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "ssq_agent.py"
SPEC = importlib.util.spec_from_file_location("ssq_agent", MODULE_PATH)
assert SPEC and SPEC.loader
ssq_agent = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ssq_agent
SPEC.loader.exec_module(ssq_agent)


class SsqAgentTests(unittest.TestCase):
    def test_determine_prize_level(self) -> None:
        self.assertEqual(ssq_agent.determine_prize_level(6, True), "一等奖")
        self.assertEqual(ssq_agent.determine_prize_level(6, False), "二等奖")
        self.assertEqual(ssq_agent.determine_prize_level(5, True), "三等奖")
        self.assertEqual(ssq_agent.determine_prize_level(4, True), "四等奖")
        self.assertEqual(ssq_agent.determine_prize_level(3, True), "五等奖")
        self.assertEqual(ssq_agent.determine_prize_level(0, True), "六等奖")
        self.assertIsNone(ssq_agent.determine_prize_level(2, False))

    def test_compare_selected_numbers_against_draw(self) -> None:
        selected = [
            {"reds": [3, 6, 13, 21, 28, 29], "blue": 6},
            {"reds": [3, 6, 13, 21, 28, 30], "blue": 6},
            {"reds": [1, 2, 3, 4, 5, 6], "blue": 16},
        ]
        draw = ssq_agent.DrawResult(
            issue="2026033",
            open_date=date(2026, 3, 26),
            reds=[3, 6, 13, 21, 28, 29],
            blue=6,
            sale_money=397739574,
            prize_pool_money=2203784926,
        )
        comparisons, summary = ssq_agent.compare_selected_numbers_against_draw(selected, draw)
        self.assertEqual(comparisons[0]["prize"], "一等奖")
        self.assertEqual(comparisons[1]["prize"], "三等奖")
        self.assertIsNone(comparisons[2]["prize"])
        self.assertTrue(summary["is_winner"])
        self.assertEqual(summary["winning_entries"], 2)
        self.assertEqual(summary["highest_prize"], "一等奖")
        self.assertEqual(summary["prize_breakdown"], {"一等奖": 1, "三等奖": 1})
        self.assertEqual(summary["fixed_prize_total"], 3000)
        self.assertEqual(summary["floating_prize_entries"], 1)

    def test_default_recommendation_count_is_five(self) -> None:
        config = ssq_agent.deep_merge(ssq_agent.DEFAULT_CONFIG, {})
        self.assertEqual(config["recommendation"]["count"], 5)

    def test_extract_official_latest_draw(self) -> None:
        html = """
        <div class="ssqRed-dom">[03,06,13,21,28,29]</div>
        <div class="ssqBlue-dom">[06]</div>
        <div class="ssqQh-dom">2026033</div>
        <div class="ssqPool-dom">2,203,784,926</div>
        <div class="ssqSales-dom">397,739,574</div>
        <div class="ssqXqLink-dom">/c/2026/03/26/527544.shtml</div>
        """
        draw = ssq_agent.extract_official_latest_draw(html)
        self.assertEqual(draw.issue, "2026033")
        self.assertEqual(draw.open_date.isoformat(), "2026-03-26")
        self.assertEqual(draw.reds, [3, 6, 13, 21, 28, 29])
        self.assertEqual(draw.blue, 6)

    def test_extract_official_latest_draw_from_api(self) -> None:
        payload = [
            {
                "code": "2026032",
                "date": "2026-03-24",
                "red": "01 03 11 18 31 33",
                "blue": "02",
                "sales": "392702390",
                "pool": "2278784926",
            },
            {
                "code": "2026033",
                "date": "2026-03-26",
                "red": "03 06 13 21 28 29",
                "blue": "06",
                "sales": "397739574",
                "pool": "2203784926",
            },
        ]
        draw = ssq_agent.extract_official_latest_draw_from_api(payload)
        self.assertEqual(draw.issue, "2026033")
        self.assertEqual(draw.open_date.isoformat(), "2026-03-26")
        self.assertEqual(draw.reds, [3, 6, 13, 21, 28, 29])
        self.assertEqual(draw.blue, 6)

    def test_parse_request_count(self) -> None:
        self.assertEqual(ssq_agent.parse_request_count("来1组双色球", 5), 1)
        self.assertEqual(ssq_agent.parse_request_count("给我五组号码", 1), 5)
        self.assertEqual(ssq_agent.parse_request_count("双色球推荐", 5), 5)

    def test_increment_issue_rolls_forward(self) -> None:
        self.assertEqual(ssq_agent.increment_issue("2026033", 1), "2026034")
        self.assertEqual(ssq_agent.increment_issue("2026999", 1), "2027001")

    def test_purchase_window_matches_draw_day_before_close(self) -> None:
        config = ssq_agent.deep_merge(ssq_agent.DEFAULT_CONFIG, {})
        tz = ZoneInfo(config["lottery"]["timezone"])

        sunday_evening = datetime(2026, 3, 29, 19, 30, tzinfo=tz)
        monday_evening = datetime(2026, 3, 30, 19, 30, tzinfo=tz)
        closed_time = datetime(2026, 3, 29, 20, 1, tzinfo=tz)

        self.assertTrue(ssq_agent.in_purchase_window(sunday_evening, config))
        self.assertFalse(ssq_agent.in_purchase_window(monday_evening, config))
        self.assertFalse(ssq_agent.in_purchase_window(closed_time, config))

    def test_compute_target_issue_uses_next_draw_day(self) -> None:
        config = ssq_agent.deep_merge(ssq_agent.DEFAULT_CONFIG, {})
        tz = ZoneInfo(config["lottery"]["timezone"])
        history = [
            ssq_agent.DrawResult(
                issue="2026033",
                open_date=date(2026, 3, 26),
                reds=[3, 6, 13, 21, 28, 29],
                blue=6,
                sale_money=397739574,
                prize_pool_money=2203784926,
            )
        ]
        now_local = datetime(2026, 3, 28, 12, 0, tzinfo=tz)
        target_issue, target_date = ssq_agent.compute_target_issue(now_local, history, config)
        self.assertEqual(target_issue, "2026034")
        self.assertEqual(target_date.isoformat(), "2026-03-29")


if __name__ == "__main__":
    unittest.main()
