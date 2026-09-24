"""真实 Docker 的样例只读诊断专项；变更阶段与只读观测阶段明确分离。

运行：PYTHONPATH=apps/api .venv/bin/python scripts/verify_sandbox_sample_reconciliation.py
自有容器及源由验收脚本显式收尾，诊断函数仅允许 ls/inspect 和元数据读取。
"""

import asyncio
import errno
import json
import stat
from dataclasses import replace
from unittest.mock import patch

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.docker import docker_client as client
from app.services.runtime.sandbox import sandbox_sample as samples
from app.services.runtime.sandbox import sandbox_sample_command as commands
from app.services.runtime.sandbox.sandbox_identity import parse_created_container_id
from app.services.runtime.sandbox.sandbox_isolation_policy import confirm_sandbox_isolation_policy
from app.services.runtime.sandbox.sandbox_sample_reconciliation import reconcile_sample_command
from app.services.runtime.sandbox.sandbox_stop import stop_and_confirm_sandbox


def source_evidence(sample):
    """记录本轮已知私有目录，不跟随测试替换出的链接。"""

    result = {}
    paths = [sample.root.parent]
    paths.extend(sample.root.parent.iterdir())
    for path in list(paths):
        if path != sample.root.parent and stat.S_ISDIR(path.lstat().st_mode):
            paths.extend(path.iterdir())
    for path in paths:
        info = path.lstat()
        result[str(path)] = (
            info.st_dev, info.st_ino, info.st_mode, info.st_mtime_ns,
            path.read_bytes() if stat.S_ISREG(info.st_mode) else None,
        )
    return result


async def main():
    command = CommandRequest(argv=['/usr/local/bin/python', '-c', 'import time; time.sleep(600)'])
    audit = {'container_ids': [], 'reports': []}
    actual_create = commands.create_sandbox_container
    actual_client = client._run_docker_client
    recovery = None

    async def lost_create(*, spec):
        audit['spec'] = spec
        receipt = await actual_create(spec=spec)
        audit['container_ids'].append(parse_created_container_id(receipt))
        raise OSError('injected create receipt loss')

    async def container_evidence():
        result = {}
        for cid in audit['container_ids']:
            if await client.is_sandbox_container_absent(container_id=cid):
                result[cid] = 'absent'
            else:
                payload = json.loads(await client.inspect_sandbox_container_by_id(container_id=cid))[0]
                result[cid] = {key: payload[key] for key in ('Id', 'Name', 'State', 'Config', 'HostConfig', 'Mounts')}
        return result

    async def observe(label, evidence, expected_container, expected_sample, *, fail_docker=False):
        before_files = source_evidence(recovery.sample)
        before_containers = await container_evidence()
        before_registry = dict(samples._ACTIVE_SAMPLES)
        calls = []
        async def readonly_client(arguments):
            assert arguments[:2] in (('container', 'ls'), ('container', 'inspect'))
            calls.append(arguments)
            if fail_docker:
                raise PermissionError('injected read failure')
            return await actual_client(arguments)
        with patch.object(client, '_run_docker_client', readonly_client):
            snapshot = await reconcile_sample_command(recovery=evidence)
        assert snapshot.container_status == expected_container
        assert snapshot.sample_status == expected_sample
        assert snapshot.recovery is evidence and snapshot.recovery.command is evidence.command
        assert source_evidence(recovery.sample) == before_files
        assert await container_evidence() == before_containers
        assert samples._ACTIVE_SAMPLES == before_registry
        if evidence.container_id is not None:
            assert all(call[-1] != evidence.container_name for call in calls)
        report = {'case': label, 'container': snapshot.container_status,
                  'sample': snapshot.sample_status, 'id_source': snapshot.id_source,
                  'readonly_queries': len(calls), 'resources_unchanged': True}
        audit['reports'].append(report)
        print(json.dumps(report), flush=True)
        return snapshot

    primary_error = None
    try:
        # 从实际编排失败取得恢复对象；不手工伪造注册或容器身份。
        with patch.object(commands, 'create_sandbox_container', lost_create):
            try:
                await commands.run_sample_sandbox_command(request=command)
            except commands.SampleCommandUnconfirmed as error:
                recovery = error.recovery
        assert recovery is not None and recovery.container_id is None
        assert recovery.sample is not None and recovery.sample.root.exists()
        print(json.dumps({'execution_token': recovery.execution_token,
                          'source': str(recovery.sample.root)}), flush=True)
        cid = audit['container_ids'][0]
        discovered = await observe('unknown_created', recovery, 'created', 'identity_matches_record')
        assert discovered.id_source == 'discovered' and discovered.container_id == cid
        assert recovery.container_id is None
        known = replace(recovery, container_id=cid, phase='executing')
        await observe('known_created', known, 'created', 'identity_matches_record')

        # 生命周期变化仅由脚本发起；诊断本身不调用 start/stop/rm。
        text = await client.inspect_sandbox_container_by_id(container_id=cid)
        confirm_sandbox_isolation_policy(
            request=command, execution_token=recovery.execution_token, container_id=cid,
            inspect_stdout=text, sample=recovery.sample,
        )
        await client.start_sandbox_container(container_id=cid)
        await observe('known_running_despite_old_stop', replace(known, stop_confirmed=True),
                      'running', 'identity_matches_record')
        await observe('unknown_running_not_adopted', recovery, 'unconfirmed', 'identity_matches_record')
        await stop_and_confirm_sandbox(request=command, execution_token=recovery.execution_token,
                                       expected_container_id=cid)
        await observe('known_exited_no_command_invented', known, 'exited', 'identity_matches_record')
        await client.remove_sandbox_container(container_id=cid)
        assert await client.is_sandbox_container_absent(container_id=cid)
        await observe('known_absent', known, 'absent', 'identity_matches_record')
        await observe('unknown_absent_stays_unknown', recovery, 'unconfirmed', 'identity_matches_record')

        # 同名目标由本轮脚本重建；已知旧 ID 绝不能被该新容器替代。
        samples.confirm_sandbox_sample_source(recovery.sample)
        replacement = parse_created_container_id(await actual_create(spec=audit['spec']))
        audit['container_ids'].append(replacement)
        assert replacement != cid
        await observe('same_name_replacement_not_adopted', known, 'absent', 'identity_matches_record')
        replacement_record = replace(known, container_id=replacement)

        held = recovery.sample.root.with_name('held')
        recovery.sample.root.rename(held)
        try:
            await observe('source_path_missing', replacement_record, 'created', 'missing')
            recovery.sample.root.symlink_to(held, target_is_directory=True)
            try:
                await observe('source_link_replacement', replacement_record, 'created', 'identity_changed')
            finally:
                recovery.sample.root.unlink()
        finally:
            held.rename(recovery.sample.root)

        real_open = samples.os.open
        def denied(path, *args, **kwargs):
            if path == recovery.sample.root.name:
                raise PermissionError(errno.EACCES, 'injected source permission failure')
            return real_open(path, *args, **kwargs)
        with patch.object(samples.os, 'open', denied):
            await observe('source_read_failure', replacement_record, 'created', 'unconfirmed')
        await observe('docker_read_failure', replacement_record, 'unconfirmed',
                      'identity_matches_record', fail_docker=True)
    except BaseException as error:
        primary_error = error
        raise
    finally:
        try:
            # 故障现场由明确拥有本轮资源的脚本处理，不利用诊断快照授权删除。
            if recovery is not None:
                for cid in audit['container_ids']:
                    if not await client.is_sandbox_container_absent(container_id=cid):
                        stopped = await stop_and_confirm_sandbox(
                            request=command, execution_token=recovery.execution_token,
                            expected_container_id=cid,
                        )
                        assert stopped.stopped
                        await client.remove_sandbox_container(container_id=cid)
                    assert await client.is_sandbox_container_absent(container_id=cid)
                if not audit['container_ids'] and recovery.create_attempted:
                    raise RuntimeError('unknown creation outcome; preserve sample')
                if recovery.sample is not None:
                    samples.cleanup_sandbox_sample(recovery.sample)
                    assert not recovery.sample.root.parent.exists()
            assert not [task for task in asyncio.all_tasks() if task.get_name() in (
                'docker-client-spawn', 'docker-client-collect', 'docker-client-cleanup',
            )]
            print(json.dumps({'passed_observations': len(audit['reports']),
                              'container_ids_absent': audit['container_ids'],
                              'sample_cleaned': recovery is not None and recovery.sample is not None,
                              'owned_async_tasks_absent': True}), flush=True)
        except BaseException as cleanup_error:
            if primary_error is not None:
                primary_error.add_note(f'cleanup unconfirmed: {type(cleanup_error).__name__}')
            else:
                raise


if __name__ == '__main__':
    asyncio.run(main())
