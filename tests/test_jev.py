from __future__ import annotations

import json
import unittest

import httpx

from pia_harness.jev import JevDecisionAdapter, JevDecisionError
from pia_harness.memory import MemoryReviewRequest


def adapter(handler) -> JevDecisionAdapter:
    return JevDecisionAdapter(
        api_key="synthetic-key",
        sync_client=httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://openrouter.ai"
        ),
    )


class JevDecisionAdapterTest(unittest.TestCase):
    def test_memory_choice_is_independent_of_writer(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            self.assertEqual("Memory rules", payload["state"]["instruction"])
            return httpx.Response(
                200,
                json={
                    "answers": {
                        "memory_change": {"type": "choice", "choice": "unchanged"}
                    }
                },
            )

        self.assertFalse(
            adapter(handler).decide_memory_change(
                MemoryReviewRequest("Memory rules", "", (), 4000, False)
            )
        )

    def test_rewrite_and_bad_answers(self) -> None:
        def rewrite(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "answers": {
                        "memory_change": {"type": "choice", "choice": "rewrite"}
                    }
                },
            )

        request = MemoryReviewRequest("Memory rules", "", (), 4000, False)
        self.assertTrue(adapter(rewrite).decide_memory_change(request))
        for response in (
            httpx.Response(500),
            httpx.Response(200, json={"answers": {}}),
            httpx.Response(
                200,
                json={"answers": {"memory_change": {"type": "choice", "choice": "x"}}},
            ),
        ):
            with (
                self.subTest(status=response.status_code),
                self.assertRaises(JevDecisionError),
            ):
                adapter(lambda _request, r=response: r).decide_memory_change(request)


if __name__ == "__main__":
    unittest.main()
