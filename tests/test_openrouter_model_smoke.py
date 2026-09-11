from __future__ import annotations

import asyncio
import json
import unittest

from pia_harness import (
    GeneratedAnswer,
    MemoryAction,
    MemoryReviewAction,
    MemoryReviewOutput,
    OpenRouterModelError,
    SummaryOutput,
)
from scripts.smoke_openrouter_model import SmokeConfig, cli, run_smoke


FAKE_KEY = "sk-or-v1-TEST-ONLY-smoke-secret"
MODEL = "vendor/exact-model"


class FakeAdapter:
    def __init__(
        self,
        *,
        answers=None,
        memories=None,
        summary=None,
    ) -> None:
        self.answers = iter(answers or passing_answers())
        self.memories = iter(
            memories
            or (
                MemoryReviewOutput(
                    MemoryReviewAction.REPLACE,
                    "사용자는 짧은 답변을 선호한다.",
                    ("답변 선호를 추가했어요.",),
                ),
                MemoryReviewOutput(MemoryReviewAction.UNCHANGED),
            )
        )
        self.summary = summary or SummaryOutput("한국어 요약", MODEL, 10)
        self.closed = False
        self.answer_calls = 0
        self.memory_calls = 0
        self.summary_calls = 0

    def count_input_tokens(self, parts):
        return 10

    async def generate_answer(self, context):
        self.answer_calls += 1
        value = next(self.answers)
        if isinstance(value, BaseException):
            raise value
        return value

    def review_memory(self, request):
        self.memory_calls += 1
        value = next(self.memories)
        if isinstance(value, BaseException):
            raise value
        return value

    def summarize(self, request):
        self.summary_calls += 1
        if isinstance(self.summary, BaseException):
            raise self.summary
        return self.summary

    async def aclose(self):
        self.closed = True


def answer(
    action: MemoryAction,
    *,
    confirmed: bool = False,
    model: str = MODEL,
) -> GeneratedAnswer:
    return GeneratedAnswer(
        text="한국어 synthetic 답변",
        model_id=model,
        estimated_total_tokens=20,
        memory_action=action,
        delete_all_confirmed=confirmed,
    )


def passing_answers() -> tuple[GeneratedAnswer, ...]:
    return (
        answer(MemoryAction.NONE),
        answer(MemoryAction.UPDATE),
        answer(MemoryAction.FORGET),
        answer(MemoryAction.DELETE_ALL),
        answer(MemoryAction.DELETE_ALL, confirmed=True),
    )


def parsed(lines: list[str]) -> list[dict]:
    return [json.loads(line) for line in lines]


class OpenRouterModelSmokeTest(unittest.IsolatedAsyncioTestCase):
    async def test_all_eight_scenarios_pass_and_adapter_closes(self) -> None:
        adapter = FakeAdapter()
        lines: list[str] = []

        exit_code = await run_smoke(
            config=SmokeConfig(MODEL, 262_144),
            api_key=FAKE_KEY,
            emit=lines.append,
            adapter_factory=lambda **values: adapter,
        )

        results = parsed(lines)
        self.assertEqual(0, exit_code)
        self.assertEqual(9, len(results))
        self.assertTrue(all(item["status"] == "PASS" for item in results))
        self.assertEqual(5, adapter.answer_calls)
        self.assertEqual(2, adapter.memory_calls)
        self.assertEqual(1, adapter.summary_calls)
        self.assertTrue(adapter.closed)
        self.assertEqual([MODEL], results[-1]["observed_models"])
        self.assertEqual(8, results[-1]["total"])
        self.assertIsNone(results[6]["output_text"])

    async def test_wrong_action_and_mixed_response_models_fail_aggregate(self) -> None:
        answers = list(passing_answers())
        answers[0] = answer(MemoryAction.UPDATE)
        answers[1] = answer(MemoryAction.UPDATE, model="vendor/other-model")
        adapter = FakeAdapter(answers=answers)
        lines: list[str] = []

        exit_code = await run_smoke(
            config=SmokeConfig(MODEL, 262_144),
            api_key=FAKE_KEY,
            emit=lines.append,
            adapter_factory=lambda **values: adapter,
        )

        results = parsed(lines)
        self.assertEqual(1, exit_code)
        self.assertEqual("FAIL", results[0]["status"])
        self.assertFalse(results[-1]["model_consistent"])
        self.assertEqual(
            [MODEL, "vendor/other-model"], results[-1]["observed_models"]
        )

    async def test_adapter_error_is_safe_and_does_not_stop_later_scenarios(self) -> None:
        answers = list(passing_answers())
        answers[0] = OpenRouterModelError(
            "openrouter.http_error", status=404
        )
        adapter = FakeAdapter(answers=answers)
        lines: list[str] = []

        exit_code = await run_smoke(
            config=SmokeConfig(MODEL, 262_144),
            api_key=FAKE_KEY,
            emit=lines.append,
            adapter_factory=lambda **values: adapter,
        )

        results = parsed(lines)
        self.assertEqual(1, exit_code)
        self.assertEqual("openrouter.http_error", results[0]["error_event"])
        self.assertEqual(404, results[0]["error_status"])
        self.assertEqual(5, adapter.answer_calls)
        self.assertEqual(2, adapter.memory_calls)
        self.assertEqual(1, adapter.summary_calls)
        self.assertTrue(adapter.closed)
        output = "\n".join(lines)
        self.assertNotIn(FAKE_KEY, output)
        self.assertNotIn("Authorization", output)

    async def test_memory_and_summary_contract_failures_are_reported(self) -> None:
        adapter = FakeAdapter(
            memories=(
                MemoryReviewOutput(
                    MemoryReviewAction.REPLACE,
                    "x" * 4_001,
                    (),
                ),
                MemoryReviewOutput(
                    MemoryReviewAction.UNCHANGED,
                    "must be null",
                    (),
                ),
            ),
            summary=SummaryOutput("", MODEL, None),
        )
        lines: list[str] = []

        exit_code = await run_smoke(
            config=SmokeConfig(MODEL, 262_144),
            api_key=FAKE_KEY,
            emit=lines.append,
            adapter_factory=lambda **values: adapter,
        )

        results = parsed(lines)
        self.assertEqual(1, exit_code)
        self.assertEqual("FAIL", results[5]["status"])
        self.assertEqual("FAIL", results[6]["status"])
        self.assertEqual("FAIL", results[7]["status"])

    async def test_replace_needs_a_document_and_a_reported_change(self) -> None:
        cases = (
            MemoryReviewOutput(MemoryReviewAction.REPLACE, "", ("추가했어요.",)),
            MemoryReviewOutput(
                MemoryReviewAction.REPLACE, "사용자는 짧은 답변을 선호한다.", ()
            ),
        )
        for replacement in cases:
            with self.subTest(memory_text=replacement.memory_text):
                adapter = FakeAdapter(
                    memories=(
                        replacement,
                        MemoryReviewOutput(MemoryReviewAction.UNCHANGED),
                    )
                )
                lines: list[str] = []

                exit_code = await run_smoke(
                    config=SmokeConfig(MODEL, 262_144),
                    api_key=FAKE_KEY,
                    emit=lines.append,
                    adapter_factory=lambda _adapter=adapter, **values: _adapter,
                )

                results = parsed(lines)
                self.assertEqual(1, exit_code)
                self.assertEqual("FAIL", results[5]["status"])
                self.assertEqual("PASS", results[6]["status"])

    async def test_cancellation_propagates_and_adapter_closes(self) -> None:
        started = asyncio.Event()

        class CancellingAdapter(FakeAdapter):
            async def generate_answer(self, context):
                started.set()
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        adapter = CancellingAdapter()
        task = asyncio.create_task(
            run_smoke(
                config=SmokeConfig(MODEL, 262_144),
                api_key=FAKE_KEY,
                emit=lambda line: None,
                adapter_factory=lambda **values: adapter,
            )
        )
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(adapter.closed)


class OpenRouterModelSmokeCliTest(unittest.TestCase):
    def test_invalid_key_alias_and_numbers_exit_two_without_adapter(self) -> None:
        calls: list[dict] = []

        def factory(**values):
            calls.append(values)
            return FakeAdapter()

        cases = (
            ({}, ["--model", MODEL, "--context-limit", "262144"]),
            (
                {"OPENROUTER_API_KEY": FAKE_KEY},
                ["--model", "openrouter/free", "--context-limit", "262144"],
            ),
            (
                {"OPENROUTER_API_KEY": FAKE_KEY},
                ["--model", "~vendor/latest", "--context-limit", "262144"],
            ),
            (
                {"OPENROUTER_API_KEY": FAKE_KEY},
                ["--model", MODEL, "--context-limit", "0"],
            ),
            (
                {"OPENROUTER_API_KEY": FAKE_KEY},
                [
                    "--model",
                    MODEL,
                    "--context-limit",
                    "262144",
                    "--timeout-seconds",
                    "0",
                ],
            ),
        )
        for environ, argv in cases:
            with self.subTest(argv=argv):
                lines: list[str] = []
                self.assertEqual(
                    2,
                    cli(
                        argv,
                        environ=environ,
                        emit=lines.append,
                        adapter_factory=factory,
                    ),
                )
                self.assertEqual("smoke.invalid_configuration", parsed(lines)[0]["error_event"])
        self.assertEqual([], calls)

    def test_output_uses_only_documented_json_fields(self) -> None:
        adapter = FakeAdapter()
        lines: list[str] = []
        self.assertEqual(
            0,
            cli(
                ["--model", MODEL, "--context-limit", "262144"],
                environ={"OPENROUTER_API_KEY": FAKE_KEY},
                emit=lines.append,
                adapter_factory=lambda **values: adapter,
            ),
        )
        for item in parsed(lines[:-1]):
            self.assertEqual(
                {
                    "scenario",
                    "status",
                    "elapsed_ms",
                    "response_model",
                    "expected_action",
                    "actual_action",
                    "expected_confirmed",
                    "actual_confirmed",
                    "total_tokens",
                    "completion_tokens",
                    "output_text",
                    "error_event",
                    "error_status",
                    "error_type",
                },
                set(item),
            )


if __name__ == "__main__":
    unittest.main()
