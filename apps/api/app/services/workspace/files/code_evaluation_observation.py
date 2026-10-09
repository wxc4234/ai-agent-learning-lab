"""离线评测观测；计时不改变执行结果，用量未知不等于免费。"""

import asyncio
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from math import ceil, isfinite
from time import perf_counter
from collections.abc import Callable

import httpx


@dataclass
class Usage:
    # attempts记录真实进入传输的次数；只有生成器完整校验后才接受报告用量。
    attempts: int = 0
    validated_requests: int = 0
    prompt_tokens: int | None = None
    total_tokens: int | None = None


class Observation:
    def __init__(self, clock: Callable[[], float] = perf_counter):
        self.clock = clock
        self.spans: list[dict] = []
        self.usage: dict[tuple[str, str], Usage] = {}

    def entry(self, phase: str, sample: str) -> Usage:
        return self.usage.setdefault((phase, sample), Usage())

    @contextmanager
    def span(self, phase: str, sample: str, stage: str):
        start = self.clock()
        status = "ok"
        try:
            yield
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except BaseException:
            status = "error"
            raise
        finally:
            elapsed = self.clock() - start
            if not isfinite(elapsed) or elapsed < 0:
                raise ValueError("invalid_monotonic_duration")
            self.spans.append({"phase": phase, "sample": sample, "stage": stage,
                                   "status": status, "duration_ms": elapsed * 1000})

    def accept_usage(self, phase: str, sample: str, generated) -> None:
        entry = self.entry(phase, sample)
        count, prompt, total = generated.request_count, generated.prompt_tokens, generated.total_tokens
        if type(count) is not int or count < 1 or entry.validated_requests:
            raise ValueError("invalid_or_duplicate_usage")
        if not (prompt is None and total is None) and (
            type(prompt) is not int or type(total) is not int or prompt < 0 or total < prompt
        ):
            raise ValueError("invalid_usage")
        entry.validated_requests = count
        entry.prompt_tokens, entry.total_tokens = prompt, total

    def transport(self, phase: str, sample: str, inner: httpx.AsyncBaseTransport):
        observation = self

        class CountedTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                observation.entry(phase, sample).attempts += 1
                return await inner.handle_async_request(request)

            async def aclose(self):
                await inner.aclose()

        return CountedTransport()

    def report(self) -> dict:
        groups = {}
        for span in self.spans:
            key = (span["phase"], span["stage"], span["status"])
            groups.setdefault(key, []).append(span["duration_ms"])
        summaries = []
        for (phase, stage, status), values in sorted(groups.items()):
            ordered = sorted(values)
            summaries.append({"phase": phase, "stage": stage, "status": status, "count": len(values),
                                  "mean_ms": sum(values) / len(values), "p50_ms": ordered[ceil(len(values) * .5) - 1],
                                  "p95_ms": ordered[ceil(len(values) * .95) - 1]})
        usage_rows = []
        for (phase, sample), entry in sorted(self.usage.items()):
            complete = entry.attempts > 0 and entry.attempts == entry.validated_requests and entry.total_tokens is not None
            usage_rows.append(dict(phase=phase, sample=sample, **asdict(entry),
                                   state="reported" if complete else "not_requested" if entry.attempts == 0 else "unknown"))
        totals = []
        for phase in sorted({row["phase"] for row in usage_rows}):
            rows = [row for row in usage_rows if row["phase"] == phase]
            unknown = sum(row["state"] == "unknown" for row in rows)
            attempts = sum(row["attempts"] for row in rows)
            totals.append({"phase": phase, "samples": len(rows), "attempts": attempts, "unknown_samples": unknown,
                               "prompt_tokens": None if unknown else sum(row["prompt_tokens"] or 0 for row in rows),
                               "total_tokens": None if unknown else sum(row["total_tokens"] or 0 for row in rows),
                               "cost": None, "cost_state": "unknown"})
        return {"schema_version": 1, "clock": "monotonic", "percentile": "nearest-rank",
                    "spans": self.spans.copy(), "latency": summaries, "usage": usage_rows, "usage_totals": totals,
                    "timing_scope": "nested spans overlap; do not sum stage percentiles",
                    "environment": "local PostgreSQL and controlled HTTPX; not provider performance"}
