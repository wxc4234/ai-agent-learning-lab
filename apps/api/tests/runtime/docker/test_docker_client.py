"""Docker 客户端专项：受控进程与真实 OS 管道，不依赖 daemon。"""

import asyncio
import sys
from pathlib import Path

import pytest

from app.services.runtime.docker import docker_client as client


TOKEN = "a" * 32


def run(scenario):
    asyncio.run(asyncio.wait_for(scenario(), 5))


def assert_no_workers():
    assert not [task for task in asyncio.all_tasks()
                if not task.done() and task.get_name().startswith(
                    ("docker-client-", "command-stdout-", "command-stderr-")
                )]


class Process:
    """kill 与 reap 分开，防止测试将发送信号误认为回收完成。"""

    def __init__(self, *, output=b"[]", error=b"", code=0, running=False):
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdout.feed_data(output)
        self.stderr.feed_data(error)
        self.returncode = None if running else code
        self.killed = asyncio.Event()
        self.release = asyncio.Event()
        self.waited = False
        if not running:
            self.release.set()
            self.stdout.feed_eof()
            self.stderr.feed_eof()

    def kill(self):
        self.killed.set()
        self.returncode = -9
        self.stdout.feed_eof()
        self.stderr.feed_eof()

    async def wait(self):
        await self.release.wait()
        self.waited = True
        return self.returncode


def install(monkeypatch, process):
    calls = []

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return process

    monkeypatch.setattr(client.asyncio, "create_subprocess_exec", spawn)
    return calls


def test_fixed_boundary_and_success(monkeypatch):
    async def scenario():
        process = Process()
        calls = install(monkeypatch, process)
        monkeypatch.setenv("DOCKER_HOST", "tcp://untrusted:2375")
        monkeypatch.setenv("DOCKER_CONTEXT", "untrusted")
        monkeypatch.setenv("SECRET", "must-not-inherit")
        assert await client.inspect_sandbox_container(execution_token=TOKEN) == "[]"
        args, kwargs = calls[0]
        assert args == (client.DOCKER_EXECUTABLE, f"--host={client.DOCKER_HOST}",
                        "container", "inspect", f"agent-sandbox-{TOKEN}")
        assert Path(args[0]).is_absolute()
        assert kwargs == {"stdin": asyncio.subprocess.DEVNULL,
                          "stdout": asyncio.subprocess.PIPE,
                          "stderr": asyncio.subprocess.PIPE, "cwd": "/",
                          "env": {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()),
                                  "LANG": "C", "LC_ALL": "C"}, "limit": 4096}
        assert process.waited and not process.killed.is_set()
        assert_no_workers()
    run(scenario)


@pytest.mark.parametrize("token", [None, True, 12, b"a" * 32, "", "A" * 32,
                                   "a" * 31, "a" * 33, "a" * 32 + "\n", "--help", "../x"])
def test_bad_token_never_spawns(monkeypatch, token):
    async def scenario():
        calls = install(monkeypatch, Process())
        with pytest.raises(ValueError):
            await client.inspect_sandbox_container(execution_token=token)
        assert calls == []
    run(scenario)


@pytest.mark.parametrize("output,error,code,expected", [
    (b"[]", b"private stderr", 1, "docker_client_failed"),
    (b"[]", b"", -9, "docker_client_failed"),
    (b"x" * 65537, b"", 0, "docker_response_unusable"),
    (b"[]", b"x" * 65537, 0, "docker_response_unusable"),
    (b"\xff", b"", 0, "docker_response_unusable"),
    ("\ufffd".encode(), b"", 0, "docker_response_unusable"),
])
def test_failed_or_unusable_response(monkeypatch, output, error, code, expected):
    async def scenario():
        process = Process(output=output, error=error, code=code)
        install(monkeypatch, process)
        with pytest.raises(client.DockerClientError) as caught:
            await client.inspect_sandbox_container(execution_token=TOKEN)
        assert caught.value.code == expected
        assert str(caught.value) == expected
        assert process.waited
        assert process.stdout.at_eof() and process.stderr.at_eof()
        assert_no_workers()
    run(scenario)


@pytest.mark.parametrize("failure,expected", [
    (FileNotFoundError("private path"), "docker_client_unavailable"),
    (PermissionError("private path"), "docker_client_unavailable"),
    (RuntimeError("private detail"), "docker_client_failed"),
])
def test_spawn_failure_is_sanitized(monkeypatch, failure, expected):
    async def scenario():
        async def spawn(*args, **kwargs):
            raise failure
        monkeypatch.setattr(client.asyncio, "create_subprocess_exec", spawn)
        with pytest.raises(client.DockerClientError) as caught:
            await client.inspect_sandbox_container(execution_token=TOKEN)
        assert str(caught.value) == expected
        assert caught.value.__suppress_context__
        assert_no_workers()
    run(scenario)


@pytest.mark.parametrize("during_spawn", [False, True])
@pytest.mark.parametrize("timeout", [False, True])
def test_timeout_cancel_and_repeated_cancel_wait_for_reap(monkeypatch, during_spawn, timeout):
    async def scenario():
        process = Process(running=True)
        entered = asyncio.Event()
        spawn_release = asyncio.Event()
        if not during_spawn:
            spawn_release.set()

        async def spawn(*args, **kwargs):
            entered.set()
            await spawn_release.wait()
            return process

        monkeypatch.setattr(client.asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(client, "DOCKER_CLIENT_TIMEOUT_SECONDS", 0.02 if timeout else 10)
        task = asyncio.create_task(client.inspect_sandbox_container(execution_token=TOKEN))
        try:
            await entered.wait()
            if timeout:
                await asyncio.sleep(0.04)
            else:
                task.cancel()
                await asyncio.sleep(0)
            assert not task.done()
            spawn_release.set()
            await process.killed.wait()
            assert not task.done(), "kill must not replace wait/reap"
            # 对取消路径再次取消，确认不会打断后台收尾。
            if not timeout:
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
            process.release.set()
            if timeout:
                with pytest.raises(client.DockerClientError, match="docker_client_timeout"):
                    await task
            else:
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert process.waited
            assert process.stdout.at_eof() and process.stderr.at_eof()
            assert_no_workers()
        finally:
            spawn_release.set()
            process.release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    run(scenario)


def test_capture_failure_still_reaps(monkeypatch):
    async def scenario():
        process = Process(running=True)
        process.release.set()
        install(monkeypatch, process)
        async def broken(**kwargs):
            raise RuntimeError("private capture failure")
        monkeypatch.setattr(client, "drain_command_streams", broken)
        with pytest.raises(client.DockerClientError, match="docker_client_failed"):
            await client.inspect_sandbox_container(execution_token=TOKEN)
        assert process.killed.is_set() and process.waited
        assert process.stdout.at_eof() and process.stderr.at_eof()
        assert_no_workers()
    run(scenario)


@pytest.mark.parametrize("program,expected", [
    ("import sys; sys.stdout.write('x'*65536); sys.stderr.write('y'*65536)", None),
    ("import sys; sys.stdout.write('x'*200000); sys.stderr.write('y'*200000)", "docker_response_unusable"),
    ("import time; time.sleep(60)", "docker_client_timeout"),
    ("import os,time; os.close(1); os.close(2); time.sleep(60)", "docker_client_timeout"),
])
def test_real_process_pipes_and_exit(monkeypatch, program, expected):
    async def scenario():
        original_spawn = asyncio.create_subprocess_exec
        processes = []
        async def spawn(*args, **kwargs):
            # 仅测试替换可执行目标；真实启动、OS管道、信号和wait保持原样。
            process = await original_spawn(sys.executable, "-c", program, **kwargs)
            processes.append(process)
            return process
        monkeypatch.setattr(client.asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(client, "DOCKER_CLIENT_TIMEOUT_SECONDS", 0.3)
        if expected:
            with pytest.raises(client.DockerClientError, match=expected):
                await client.inspect_sandbox_container(execution_token=TOKEN)
        else:
            assert await client.inspect_sandbox_container(execution_token=TOKEN) == "x" * 65536
        assert len(processes) == 1
        assert processes[0].returncode is not None
        assert processes[0].stdout.at_eof() and processes[0].stderr.at_eof()
        assert_no_workers()
    run(scenario)


def test_process_exits_between_poll_and_kill(monkeypatch):
    async def scenario():
        process = Process(running=True)
        process.release.set()
        def raced_kill():
            process.returncode = 0
            process.stdout.feed_eof()
            process.stderr.feed_eof()
            raise ProcessLookupError
        process.kill = raced_kill
        async def spawned():
            return process
        await client._finish_client(asyncio.create_task(spawned()), None)
        assert process.waited
        assert_no_workers()
    run(scenario)


def test_cleanup_failure_is_not_reported_as_success():
    async def scenario():
        async def broken():
            raise RuntimeError("reap failed")
        with pytest.raises(RuntimeError, match="reap failed"):
            await client._wait_for_cleanup(asyncio.create_task(broken()))
    run(scenario)


def test_real_process_cancellation_reaps(monkeypatch):
    async def scenario():
        original_spawn = asyncio.create_subprocess_exec
        started = asyncio.Event()
        processes = []
        async def spawn(*args, **kwargs):
            process = await original_spawn(
                sys.executable, "-c", "import time; time.sleep(60)", **kwargs
            )
            processes.append(process)
            started.set()
            return process
        monkeypatch.setattr(client.asyncio, "create_subprocess_exec", spawn)
        task = asyncio.create_task(client.inspect_sandbox_container(execution_token=TOKEN))
        try:
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert processes[0].returncode == -9
            assert processes[0].stdout.at_eof() and processes[0].stderr.at_eof()
            assert_no_workers()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    run(scenario)
