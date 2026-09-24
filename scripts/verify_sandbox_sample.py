"""自建样例只读 bind 实机验收；不接 Task、Workspace 或模型工具。

从仓库根目录运行：PYTHONPATH=apps/api .venv/bin/python scripts/verify_sandbox_sample.py
只创建本轮随机 token 的非特权容器；收尾确认容器缺失后才清理样例。
"""

import asyncio
import copy
import json
from uuid import uuid4

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.docker.docker_client import (
    _run_docker_client,
    create_sandbox_container,
    inspect_sandbox_container,
    inspect_sandbox_container_by_id,
    is_sandbox_container_absent,
    remove_sandbox_container,
    start_sandbox_container,
)
from app.services.runtime.sandbox.sandbox_identity import (
    parse_created_container_id,
    recover_created_sandbox_identity,
)
from app.services.runtime.sandbox.sandbox_isolation_policy import (
    SandboxIsolationPolicyError,
    confirm_sandbox_isolation_policy,
)
from app.services.runtime.sandbox.sandbox_sample import (
    SAMPLE_CONTENT,
    SAMPLE_FILENAME,
    SandboxSampleError,
    cleanup_sandbox_sample,
    confirm_sandbox_sample_source,
    create_sandbox_sample,
)
from app.services.runtime.sandbox.sandbox_spec import build_sample_sandbox_create_spec
from app.services.runtime.sandbox.sandbox_stop import (
    confirm_sandbox_state,
    stop_and_confirm_sandbox,
)


# 文件在宿主可写；只接受 EROFS，不能用 EACCES 代替只读挂载证据。
PROBE = r'''
import errno
import json
import os
from pathlib import Path
import socket

source = Path('/workspace/example.txt')
assert os.getuid() == os.getgid() == 10001
assert Path.cwd() == Path('/tmp')
assert source.read_bytes() == b'sandbox sample\n'
assert os.statvfs('/workspace').f_flag & os.ST_RDONLY
try:
    source.write_bytes(b'forbidden\n')
except OSError as error:
    assert error.errno == errno.EROFS, error.errno
else:
    raise AssertionError('sample write succeeded')
assert source.read_bytes() == b'sandbox sample\n'
status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
assert int(status['CapEff'].strip(), 16) == 0
assert status['NoNewPrivs'].strip() == '1'
assert status['Seccomp'].strip() == '2'
assert os.statvfs('/').f_flag & os.ST_RDONLY
for directory in ('/tmp', '/home/agent'):
    target = Path(directory) / 'sample-probe.txt'
    target.write_bytes(b'temporary')
    assert target.read_bytes() == b'temporary'
    target.unlink()
    flags = os.statvfs(directory).f_flag
    assert flags & os.ST_NOSUID and flags & os.ST_NODEV and flags & os.ST_NOEXEC
assert not Path('/var/run/docker.sock').exists()
with socket.socket() as connection:
    connection.settimeout(1)
    try:
        connection.connect(('1.1.1.1', 443))
    except OSError as error:
        assert error.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH)
    else:
        raise AssertionError('network reachable')
limits = Path('/sys/fs/cgroup')
assert (limits / 'memory.max').read_text().strip() == '134217728'
assert (limits / 'memory.swap.max').read_text().strip() == '0'
assert (limits / 'pids.max').read_text().strip() == '32'
quota, period = map(int, (limits / 'cpu.max').read_text().split())
assert quota / period == 0.5
print(json.dumps({'read': True, 'write_errno': errno.EROFS, 'cwd': str(Path.cwd()),
                  'uid': os.getuid(), 'isolation': True}))
'''


def reject_response_mutations(*, text, sample, command, token, container_id):
    """对真实响应副本注入错源/额外挂载，不创建有风险的容器。"""

    mutations = (
        ("wrong_host_source", lambda item: item['HostConfig']['Mounts'][0].update(Source='/wrong')),
        ("wrong_reported_source", lambda item: item['Mounts'][0].update(Source='/wrong')),
        ("writable", lambda item: item['Mounts'][0].update(RW=True)),
        ("extra_mount", lambda item: item['Mounts'].append(
            {'Type': 'bind', 'Source': '/wrong', 'Destination': '/extra', 'RW': False})),
        ("working_directory", lambda item: item['Config'].update(WorkingDir='/workspace')),
    )
    for name, mutate in mutations:
        payload = copy.deepcopy(json.loads(text))
        mutate(payload[0])
        try:
            confirm_sandbox_isolation_policy(
                request=command, execution_token=token, container_id=container_id,
                inspect_stdout=json.dumps(payload), sample=sample,
            )
        except SandboxIsolationPolicyError:
            continue
        raise AssertionError(f'mutation accepted: {name}')
    return [name for name, _ in mutations]


async def main():
    sample = create_sandbox_sample()
    token = uuid4().hex
    command = CommandRequest(argv=['/usr/local/bin/python', '-c', 'import time; time.sleep(600)'])
    path = sample.root / SAMPLE_FILENAME
    before = (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
    container_id = None
    create_attempted = False
    spec = None
    primary_error = None
    result = {'execution_token': token, 'source': str(sample.root)}
    # 先输出本轮定位信息，即使 daemon 失联也能保留人工恢复入口。
    print(json.dumps(result), flush=True)
    try:
        # 真实链接替换须在 create 前被拒绝，并恢复本轮自有目录。
        moved = sample.root.with_name('repository-held')
        sample.root.rename(moved)
        try:
            sample.root.symlink_to(moved, target_is_directory=True)
            try:
                build_sample_sandbox_create_spec(request=command, execution_token=token, sample=sample)
            except SandboxSampleError:
                result['symlink_rejected_before_create'] = True
            else:
                raise AssertionError('symlink accepted')
        finally:
            if sample.root.is_symlink():
                sample.root.unlink()
            moved.rename(sample.root)

        spec = build_sample_sandbox_create_spec(request=command, execution_token=token, sample=sample)
        confirm_sandbox_sample_source(sample)
        # 外部副作用从这里开始；异常不证明容器不存在。
        create_attempted = True
        container_id = parse_created_container_id(await create_sandbox_container(spec=spec))
        result['container_id'] = container_id
        text = await inspect_sandbox_container_by_id(container_id=container_id)
        confirm_sandbox_isolation_policy(
            request=command, execution_token=token, container_id=container_id,
            inspect_stdout=text, sample=sample,
        )
        result['response_mutations_rejected'] = reject_response_mutations(
            text=text, sample=sample, command=command, token=token, container_id=container_id,
        )
        result['host_mounts'] = json.loads(text)[0]['HostConfig']['Mounts']
        result['reported_mounts'] = json.loads(text)[0]['Mounts']

        # 启动边界重读同一 ID、重验配置及源身份；不放宽生产启动入口。
        text = await inspect_sandbox_container_by_id(container_id=container_id)
        confirm_sandbox_isolation_policy(
            request=command, execution_token=token, container_id=container_id,
            inspect_stdout=text, sample=sample,
        )
        before_start = confirm_sandbox_state(container_id=container_id, inspect_stdout=text, spec=spec)
        assert before_start.status == 'created'
        await start_sandbox_container(container_id=container_id)
        after_start = confirm_sandbox_state(
            container_id=container_id, spec=spec,
            inspect_stdout=await inspect_sandbox_container_by_id(container_id=container_id),
        )
        assert after_start.status == 'running'
        confirm_sandbox_sample_source(sample)
        # 仅此验收脚本使用固定探针；没有提供外部任意 exec 接口。
        probe = await _run_docker_client((
            'container', 'exec', container_id, '/usr/bin/env', '-i',
            'PATH=/usr/bin:/bin', 'HOME=/home/agent', 'TMPDIR=/tmp',
            '/usr/local/bin/python', '-c', PROBE,
        ))
        result['probe'] = json.loads(probe.streams.stdout.text)
        assert before == (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        assert before[0] == SAMPLE_CONTENT
        result['host_bytes_inode_mtime_unchanged'] = True
    except BaseException as error:
        primary_error = error
        raise
    finally:
        try:
            if create_attempted:
                if container_id is None:
                    # 只恢复归属一致、仍 created 的对象；未知状态保留现场。
                    recovered = recover_created_sandbox_identity(
                        inspect_stdout=await inspect_sandbox_container(execution_token=token), spec=spec,
                    )
                    container_id = recovered.container_id
                stopped = await stop_and_confirm_sandbox(
                    request=command, execution_token=token, expected_container_id=container_id,
                )
                assert stopped.stopped
                # 按已核对的完整 ID 删除，不强制，不按名称删除。
                await remove_sandbox_container(container_id=container_id)
                assert await is_sandbox_container_absent(container_id=container_id)
                result['container_absent'] = True
            cleanup_sandbox_sample(sample)
            assert not sample.root.parent.exists()
            result['sample_absent'] = True
        except BaseException as cleanup_error:
            result['cleanup_unconfirmed'] = True
            if primary_error is not None:
                primary_error.add_note(f'cleanup unconfirmed: {type(cleanup_error).__name__}')
            else:
                raise
        finally:
            print(json.dumps(result, indent=4), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
