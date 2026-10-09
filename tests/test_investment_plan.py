import asyncio
import json
import unittest
from datetime import UTC, datetime, timedelta

from pia_harness import InvestmentPlan, SessionStoreError, ToolCall
from pia_harness.investment_plan import PLAN_MAX_CHARS, SECTION_MAX_CHARS
from pia_harness.testing import InMemoryPlanStore

NOW = datetime(2026, 10, 9, 7, 13, tzinfo=UTC)  # 16:13 KST


class FailingStore(InMemoryPlanStore):
    def get_plan(self, user_key):
        raise SessionStoreError("down")


def call(sections, reason="이유"):
    return ToolCall(
        "plan", json.dumps({"sections": sections, "reason": reason}, ensure_ascii=False)
    )


class InvestmentPlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryPlanStore()
        self.clock = iter(NOW + timedelta(days=day) for day in range(100))
        self.plan = InvestmentPlan(self.store, now=lambda: next(self.clock))
        self.tool = self.plan.plan_tool()

    def saved(self):
        return self.store.plans.get("user", [])

    def prepare(self, sections, reason="이유"):
        return asyncio.run(self.tool.prepare("user", call(sections, reason)))

    def apply(self, sections, reason="이유"):
        prepared = self.prepare(sections, reason)
        assert prepared.action is not None, prepared.observation_text
        return asyncio.run(self.tool.execute("user", prepared.action))

    def history(self, **arguments):
        tool = self.plan.history_tool()
        result = asyncio.run(
            tool.execute("user", ToolCall("plan_history", json.dumps(arguments)), ())
        )
        return result.observation_text

    def test_every_change_waits_for_confirmation_with_its_text_and_reason(self) -> None:
        for sections in ({"market": "금리 인하 기대"}, {"portfolio": "현금 30%"}):
            with self.subTest(sections=sections):
                action = self.prepare(sections, "과열").action
                self.assertTrue(action.needs_confirmation)
                text = next(iter(sections.values()))
                self.assertIn(f"\n{text}\n", action.confirmation)
                self.assertIn("이유: 과열", action.confirmation)

    def test_versions_keep_what_changed_and_why_without_numbers_for_the_user(
        self,
    ) -> None:
        self.assertEqual("투자 계획: 아직 없음", self.plan.document("user"))
        self.assertEqual(
            "투자 계획을 저장했습니다 (바뀐 칸: 시장 판단).",
            self.apply({"market": "강세"}),
        )
        self.apply({"portfolio": "현금 20%"}, "방어")
        latest = self.saved()[-1]
        self.assertEqual(2, latest.version)
        self.assertEqual(
            {"market": "강세", "portfolio": "현금 20%"}, dict(latest.sections)
        )
        self.assertEqual(("portfolio",), latest.changed)
        document = self.plan.document("user")
        self.assertTrue(document.startswith("투자 계획 (2026-10-10 16:13 저장)\n"))
        self.assertIn("이번 변경: 목표 포트폴리오 — 방어", document)
        self.assertIn("[목표 포트폴리오]\n현금 20%", document)
        self.assertIn("[투자 목표]\n\n", document)
        self.assertNotIn("버전", document)

    def test_the_same_text_is_no_change_and_an_empty_string_clears(self) -> None:
        self.apply({"market": "강세"})
        result = self.prepare({"market": "강세", "portfolio": ""})
        self.assertIsNone(result.action)
        self.assertIn("No change", result.observation_text)
        cleared = self.prepare({"market": ""}).action
        self.assertIn("[시장 판단]\n(비움)", cleared.confirmation)
        asyncio.run(self.tool.execute("user", cleared))
        self.assertEqual("", self.saved()[-1].sections["market"])

    def test_a_change_applies_to_the_plan_as_it_is_when_it_runs(self) -> None:
        # Two drafts from the same version: the later one keeps the earlier one.
        first = self.prepare({"portfolio": "현금 20%"})
        second = self.prepare({"market": "강세"})
        asyncio.run(self.tool.execute("user", second.action))
        asyncio.run(self.tool.execute("user", first.action))
        self.assertEqual(
            {"market": "강세", "portfolio": "현금 20%"}, dict(self.saved()[-1].sections)
        )

    def test_limits_and_bad_requests_prepare_nothing(self) -> None:
        names = ("goal", "rules", "portfolio", "watchlist", "market")
        for sections, reason in (
            ({"market": "x" * (SECTION_MAX_CHARS + 1)}, "이유"),
            ({name: "x" * 1_400 for name in names}, "이유"),
            ({"holdings": "삼성전자 10주"}, "이유"),
            ({}, "이유"),
            ({"market": "강세"}, " "),
        ):
            with self.subTest(sections=list(sections), reason=reason):
                result = self.prepare(sections, reason)
                self.assertIsNone(result.action)
                self.assertTrue(result.observation_text.startswith("Not prepared"))
        self.assertGreater(5 * 1_400, PLAN_MAX_CHARS)

    def test_store_failures_come_back_as_text(self) -> None:
        prepared = self.prepare({"market": "강세"})
        tool = InvestmentPlan(FailingStore()).plan_tool()
        self.assertEqual(
            "투자 계획을 저장하지 못했습니다. 다시 요청해 주세요.",
            asyncio.run(tool.execute("user", prepared.action)),
        )

    def test_history_lists_reads_a_version_and_a_day(self) -> None:
        self.assertEqual("투자 계획 기록이 없습니다.", self.history())
        self.apply({"market": "강세"}, "첫 판단")  # 2026-10-09 16:13 KST
        self.apply({"market": "약세"}, "금리 상승")  # 2026-10-10 16:13 KST
        self.assertEqual(
            "2026-10-10 16:13:00.000000 [시장 판단] 금리 상승\n"
            "2026-10-09 16:13:00.000000 [시장 판단] 첫 판단",
            self.history(),
        )
        # A minute reads its end; an empty as_of is the list.
        self.assertIn("[시장 판단]\n강세", self.history(as_of="2026-10-09 16:13"))
        self.assertIn(
            "16:12 이전의 투자 계획이 없습니다", self.history(as_of="2026-10-09 16:12")
        )
        self.assertEqual(self.history(), self.history(as_of=""))
        self.assertIn(
            "이번 변경: 시장 판단 — 금리 상승", self.history(as_of="2026-10-10")
        )
        self.assertEqual(
            "2026-10-08 이전의 투자 계획이 없습니다.", self.history(as_of="2026-10-08")
        )
        self.assertIn("Not read", self.history(as_of="10/9"))


class SameMinuteTest(unittest.TestCase):
    def test_two_changes_in_one_minute_are_read_apart_by_their_listed_time(
        self,
    ) -> None:
        times = iter((NOW + timedelta(seconds=10), NOW + timedelta(seconds=40)))
        plan = InvestmentPlan(InMemoryPlanStore(), now=lambda: next(times))
        tool = plan.plan_tool()
        for text in ("first-synthetic-value", "second-synthetic-value"):
            prepared = asyncio.run(tool.prepare("user", call({"portfolio": text})))
            asyncio.run(tool.execute("user", prepared.action))
        history = plan.history_tool()

        def read(as_of):
            arguments = json.dumps({"as_of": as_of})
            result = asyncio.run(
                history.execute("user", ToolCall("plan_history", arguments), ())
            )
            return result.observation_text

        listed = [line.split(" [", 1)[0] for line in read("").splitlines()]
        self.assertEqual(
            ["2026-10-09 16:13:40.000000", "2026-10-09 16:13:10.000000"], listed
        )
        self.assertIn("second-synthetic-value", read(listed[0]))
        self.assertIn("first-synthetic-value", read(listed[1]))


if __name__ == "__main__":
    unittest.main()
