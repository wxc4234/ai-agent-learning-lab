"""授权项目的有界内容快照；容器只接收副本，永不挂载宿主项目。"""
import base64
import json
import os
import stat
from contextlib import ExitStack
from hashlib import sha256

from app.database import SessionLocal
from app.services.runtime.agent.tool_execution_context import load_tool_execution_context
from app.repositories.workspace.project_git_repository import read_owned_git_binding
from app.services.workspace.git.project_source import _observe_root
from app.services.workspace.files.workspace_file import _file_version
from app.services.workspace.directory.workspace_path import _parse_relative_path
from app.services.runtime.sandbox.sandbox_sample import create_project_sandbox_snapshot, SandboxSample
from app.tools.context import ToolExecutionContext

# 项目与单文件学习样例使用独立预算；超限整体拒绝，不静默遗漏文件。
MAX_FILES = 2048
MAX_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
EXCLUDED = frozenset({'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.next', '.env', '.env.local'})


def read_project_snapshot(context: ToolExecutionContext) -> dict[str, bytes]:
    """两次授权夹住全部文件读取；链接、特殊文件、变化或超限均拒绝。"""
    def binding():
        with SessionLocal() as session:
            return dict(read_owned_git_binding(session, user_id=context.user_id,
                workspace_id=context.workspace_id, task_id=context.task_id))
    source = binding()
    if source['root_path'] is None:
        raise ValueError('project_unbound')
    files: dict[str, bytes] = {}
    total = 0
    with _observe_root(source['root_path']) as root, ExitStack() as stack:
        checks = []
        root_version = _file_version(os.fstat(root.descriptor))
        def walk(directory: int, prefix: str, depth: int):
            nonlocal total
            if depth > 16:
                raise ValueError('project_snapshot_limit')
            before = os.fstat(directory)
            with os.scandir(directory) as entries:
                names = []
                for entry in entries:
                    names.append(entry.name)
                    if len(names) > 1024:
                        raise ValueError('project_snapshot_limit')
            for name in sorted(names):
                if name in EXCLUDED or name.startswith(('.env.', '.agent-changes-')):
                    continue
                path = prefix + name
                if _parse_relative_path(path).as_posix() != path:
                    raise ValueError('project_snapshot_path')
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    if sum(check[2] is not None for check in checks) >= 128:
                        raise ValueError('project_snapshot_limit')
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    stack.callback(os.close, child)
                    checks.append((directory, name, child, _file_version(info)))
                    walk(child, path + '/', depth + 1)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    if len(files) >= MAX_FILES or info.st_size > MAX_BYTES - total:
                        raise ValueError('project_snapshot_limit')
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                    try:
                        chunks, size = [], 0
                        while size <= MAX_FILE_BYTES:
                            chunk = os.read(fd, min(65536, MAX_FILE_BYTES + 1 - size))
                            if not chunk:
                                break
                            chunks.append(chunk)
                            size += len(chunk)
                        if size > MAX_FILE_BYTES:
                            raise ValueError('project_snapshot_limit')
                        data = b''.join(chunks)
                        if _file_version(os.fstat(fd)) != _file_version(info):
                            raise ValueError('project_snapshot_changed')
                    finally:
                        os.close(fd)
                    total += len(data)
                    if total > MAX_BYTES:
                        raise ValueError('project_snapshot_limit')
                    files[path] = data
                    checks.append((directory, name, None, _file_version(info)))
                else:
                    raise ValueError('project_snapshot_special_file')
            if _file_version(os.fstat(directory)) != _file_version(before):
                raise ValueError('project_snapshot_changed')
        walk(root.descriptor, '', 0)
        if binding() != source or _file_version(os.fstat(root.descriptor)) != root_version:
            raise ValueError('project_snapshot_changed')
        for parent, name, fd, version in checks:
            if ((fd is not None and _file_version(os.fstat(fd)) != version)
                    or _file_version(os.stat(name, dir_fd=parent, follow_symlinks=False)) != version):
                raise ValueError('project_snapshot_changed')
        # 根目录新增/删除也必须被最后复核发现。
        # 这些观察检测常见并发变更，不提供文件系统级瞬时快照。
    return files


def create_project_snapshot(*, context: ToolExecutionContext) -> SandboxSample:
    if load_tool_execution_context(user_id=context.user_id, conversation_id=context.conversation_id) != context:
        raise ValueError('project_context_changed')
    files = read_project_snapshot(context)
    if load_tool_execution_context(user_id=context.user_id, conversation_id=context.conversation_id) != context:
        raise ValueError('project_context_changed')
    payload = json.dumps({name: base64.b64encode(data).decode('ascii') for name, data in files.items()},
                         ensure_ascii=True, separators=(',', ':')).encode()
    return create_project_sandbox_snapshot(content=payload)


def snapshot_digest(files: dict[str, bytes]) -> str:
    """长度前缀避免路径/正文拼接歧义，摘要用于追溯而非授权。"""
    digest = sha256()
    for name, body in sorted(files.items()):
        path = name.encode()
        digest.update(len(path).to_bytes(8, 'big') + path + len(body).to_bytes(8, 'big') + body)
    return digest.hexdigest()


def has_bound_project(context: ToolExecutionContext) -> bool:
    with SessionLocal() as session:
        return read_owned_git_binding(session, user_id=context.user_id,
            workspace_id=context.workspace_id, task_id=context.task_id)['root_path'] is not None
