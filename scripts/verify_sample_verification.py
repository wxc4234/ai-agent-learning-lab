"""可信固定样例的真实Docker专项；仅脚本缩短超时并观测启动屏障。"""

import asyncio
import json
from contextlib import ExitStack
from unittest.mock import patch

from app.services.runtime.docker.docker_client import is_sandbox_container_absent
from app.services.runtime.sandbox import sandbox_execution as executor
from app.services.runtime.sandbox import sandbox_sample_command as owner
from app.services.runtime.sandbox.sandbox_sample_cleanup import cleanup_sample_command
from app.services.runtime.verification import sandbox_verification as service
from app.services.runtime.verification.contracts import VerificationRequest


PASS = '''import unittest, os
class Test(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(os.statvfs('/workspace').f_flag & os.ST_RDONLY)
        self.assertNotEqual(os.getuid(), 0)
'''
CASES = {
    'pass': (PASS, 'passed'),
    'failure': ('import unittest\nclass Test(unittest.TestCase):\n    def test_bad(self): self.fail()\n', 'failed'),
    'load_error': ('syntax error !!!', 'failed'),
    'zero': ('# no tests', 'unconfirmed'),
    'missing_report': ('raise SystemExit(0)', 'unconfirmed'),
    'large_logs': (PASS + '\nprint("x" * 100000)\n', 'unconfirmed'),
    'timeout': ('import time\ntime.sleep(600)', None),
    'cancel': ('import time\ntime.sleep(600)', None),
}


async def scenario(mode, source, outcome):
    original_prepare = service.create_sandbox_snapshot
    original_cleanup = owner.cleanup_sandbox_sample
    original_start = executor.start_sandbox_container
    samples = []
    before = {}
    started = asyncio.Event()

    def fingerprint(sample):
        path = sample.root / 'example.txt'
        info = path.stat()
        return path.read_bytes(), info.st_ino, info.st_mtime_ns

    def prepare(**kwargs):
        sample = original_prepare(**kwargs)
        samples.append(sample)
        before[sample.token] = fingerprint(sample)
        return sample

    def cleanup(sample):
        assert fingerprint(sample) == before[sample.token]
        original_cleanup(sample)

    async def start(**kwargs):
        await original_start(**kwargs)
        started.set()

    with ExitStack() as patches:
        patches.enter_context(patch.object(service, 'create_sandbox_snapshot', prepare))
        patches.enter_context(patch.object(owner, 'cleanup_sandbox_sample', cleanup))
        patches.enter_context(patch.object(executor, 'start_sandbox_container', start))
        if mode == 'timeout':
            patches.enter_context(patch.object(executor, 'COMMAND_TIMEOUT_SECONDS', 1))
        task = asyncio.create_task(service.run_sample_verification(
            request=VerificationRequest(plan_id='sample_unittest_v1'),
            trusted_test_source=source.encode(),
        ))
        if mode == 'cancel':
            await asyncio.wait_for(started.wait(), 10)
            task.cancel()
        try:
            result = await task
        except (owner.SampleCommandCancelled, owner.SampleCommandUnconfirmed) as error:
            recovery = error.recovery
            # 保留定位信息，后续断言/显式清理失败时可由原所有者核对。
            print(json.dumps({'mode': mode, 'token': recovery.execution_token,
                'container_id': recovery.container_id, 'root': str(recovery.sample_root)}), flush=True)
            assert mode in ('timeout', 'cancel')
            assert recovery.stop_confirmed is True
            assert recovery.sample is not None
            assert fingerprint(recovery.sample) == before[recovery.sample.token]
            if mode == 'timeout':
                assert recovery.execution_reason == 'timed_out'
            else:
                assert isinstance(error, owner.SampleCommandCancelled)
            cleaned = await cleanup_sample_command(recovery=recovery)
            assert cleaned.recovery.container_absent and cleaned.recovery.sample_cleaned
        else:
            assert result.verification.outcome == outcome
            assert result.execution.sample_cleaned
            assert await is_sandbox_container_absent(container_id=result.execution.cleanup.container_id)
            if mode == 'large_logs':
                assert result.verification.command.stderr_truncated
        assert len(samples) == 1 and not samples[0].root.parent.exists()
        print(json.dumps({'mode': mode, 'passed': True, 'source_unchanged': True,
            'container_absent': True, 'sample_cleaned': True}), flush=True)


async def main():
    for mode, (source, outcome) in CASES.items():
        await scenario(mode, source, outcome)


if __name__ == '__main__':
    asyncio.run(main())
