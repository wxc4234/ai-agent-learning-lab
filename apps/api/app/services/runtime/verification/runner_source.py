"""固定容器内采集程序；这里只保存源码，不在API进程执行它。"""

# 与样例测试在同一解释器内，不构成对恶意测试代码的防伪边界。
# 仅面向服务端可信自建样例；后续执行仍须验证样例身份并使用Sandbox。
UNITTEST_RUNNER_SOURCE = r'''
import json
import os
import sys
import unittest

ROOT = "/workspace"
PLAN = "sample_unittest_v1"
LIMIT = 10000

class Counts(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.events = 0
        self.counts = dict(
            successful_tests=0, failures=0, errors=0, skipped=0,
            expected_failures=0, unexpected_successes=0,
        )

    def tick(self):
        self.events += 1
        if self.events > LIMIT:
            raise RuntimeError("event limit")

    def startTest(self, test):
        self.tick()
        super().startTest(test)

    def addSuccess(self, test):
        self.tick()
        self.counts["successful_tests"] += 1

    def addFailure(self, test, err):
        self.tick()
        self.counts["failures"] += 1

    def addError(self, test, err):
        self.tick()
        self.counts["errors"] += 1

    def addSkip(self, test, reason):
        self.tick()
        self.counts["skipped"] += 1

    def addExpectedFailure(self, test, err):
        self.tick()
        self.counts["expected_failures"] += 1

    def addUnexpectedSuccess(self, test):
        self.tick()
        self.counts["unexpected_successes"] += 1

    def addSubTest(self, test, subtest, err):
        self.tick()
        if err is not None:
            key = "failures" if issubclass(err[0], test.failureException) else "errors"
            self.counts[key] += 1

    def wasSuccessful(self):
        return not any(self.counts[k] for k in ("failures", "errors", "unexpected_successes"))

def main():
    # 保存原stdout，再将普通print、sys.__stdout__和fd1写入都送往stderr。
    report_fd = os.dup(1)
    os.dup2(2, 1)
    try:
        # 显式加入可信样例根，只加载固定模块，不递归发现其他测试。
        sys.path.insert(0, ROOT)
        suite = unittest.TestLoader().loadTestsFromName("tests.test_target")
        result = Counts()
        suite.run(result)
        if result.shouldStop:
            raise RuntimeError("incomplete run")
        report = dict(plan_id=PLAN, tests_run=result.testsRun, **result.counts)
        payload = json.dumps(
            dict(version=1, complete=True, report=report),
            separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii") + b"\n"
        # 报告只有结束后一次写出；异常/中断不发出部分成功。
        os.write(report_fd, payload)
        return 0 if result.wasSuccessful() else 1
    except Exception:
        os.write(2, b"verification_runner_failed\n")
        return 2
    finally:
        os.close(report_fd)

sys.exit(main())
'''.strip()
