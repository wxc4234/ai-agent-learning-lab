"""多文件执行现场：先持久化原文与对象清单，再改目录项；恢复不覆盖外部改动。"""
import os
import stat
from contextlib import ExitStack, contextmanager
from hashlib import sha256
from uuid import uuid4

from app.services.workspace.directory.workspace_path import _parse_relative_path
from app.services.workspace.files.workspace_file import _read_bounded_bytes, _file_version
from app.services.workspace.files.workspace_file_replace import _verify_directory_chain
from app.services.workspace.git.project_source import _observe_root
from app.services.workspace.metadata.workspace_file_metadata import copy_file_metadata, read_file_metadata


def identity(info):
    return [info.st_dev, info.st_ino]


def path_parts(path: str):
    parsed = _parse_relative_path(path)
    if (not parsed.parts or parsed.as_posix() != path or len(path) > 1024
            or any(part == '.git' or part.startswith('.agent-changes-') for part in parsed.parts)):
        raise ValueError('change_set_path_rejected')
    return parsed.parts


@contextmanager
def parent_at(root_fd, path):
    parts = path_parts(path)
    with ExitStack() as stack:
        parent, links = root_fd, []
        for component in parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, child)
            links.append((parent, component, child))
            parent = child
        _verify_directory_chain(links)
        yield parent, parts[-1]
        _verify_directory_chain(links)


def read_at(parent, name, *, allow_pair=False):
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink not in ((1, 2) if allow_pair else (1,)) or info.st_uid != os.geteuid()
            or info.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
            or getattr(info, 'st_flags', 0) != 0):
        raise ValueError('change_set_file_unsupported')
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        data = _read_bounded_bytes(fd)
        if (_file_version(os.fstat(fd)) != _file_version(info)
                or _file_version(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _file_version(info)):
            raise ValueError('change_set_file_changed')
        content = data.decode('utf-8')
        if '\0' in content:
            raise ValueError('change_set_binary_rejected')
        return {'content': content, 'identity': identity(info), 'sha256': sha256(data).hexdigest()}
    finally:
        os.close(fd)


def read_paths(root_path, paths):
    with _observe_root(root_path) as root:
        result = {}
        for path in paths:
            with parent_at(root.descriptor, path) as (parent, name):
                result[path] = read_at(parent, name)
        # 第二轮核对全部输入，失败不返回部分成功。
        for path, expected in result.items():
            with parent_at(root.descriptor, path) as (parent, name):
                if read_at(parent, name) != expected:
                    raise ValueError('change_set_file_changed')
        return list(root.identity), result


def _matches(current, content, expected_identity):
    return current is None if content is None else (
        current is not None and current['content'] == content and current['identity'] == expected_identity)


def prepare(root_path, root_identity, entries, save_journal):
    """私有现场先登记，再创建完整临时文件；副作用前确认全部新旧对象清单提交。"""
    with _observe_root(root_path) as root:
        if list(root.identity) != root_identity:
            raise ValueError('change_set_root_changed')
        for item in entries:
            with parent_at(root.descriptor, item['path']) as (parent, name):
                if not _matches(read_at(parent, name), item['before'], item['identity']):
                    raise ValueError('change_set_baseline_changed')
        directory = '.agent-changes-' + uuid4().hex
        os.mkdir(directory, 0o700, dir_fd=root.descriptor)
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root.descriptor)
        journal = {'directory': directory, 'identity': identity(os.fstat(fd)), 'prepared': False, 'new': {}}
        try:
            # 此时还没有改动任何目标文件；提交确认丢失也不继续写入目标。
            save_journal(journal)
            for index, item in enumerate(entries):
                if item['after'] is None:
                    continue
                temp = os.open(f'new-{index}', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                try:
                    data = item['after'].encode()
                    while data:
                        written = os.write(temp, data)
                        if not written:
                            raise OSError('short write')
                        data = data[written:]
                    if item['before'] is not None or item.get('metadata_source'):
                        with parent_at(root.descriptor, item.get('metadata_source', item['path'])) as (parent, name):
                            original = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                            try:
                                metadata = read_file_metadata(original)
                                copy_file_metadata(source_fd=original, target_fd=temp, expected=metadata)
                            finally:
                                os.close(original)
                    os.fsync(temp)
                    journal['new'][str(index)] = identity(os.fstat(temp))
                finally:
                    os.close(temp)
            os.fsync(fd)
            journal['prepared'] = True
            save_journal(journal)
        finally:
            os.close(fd)
        return journal


@contextmanager
def scene(root_path, root_identity, journal):
    if (not isinstance(journal, dict) or not isinstance(journal.get('directory'), str)
            or not journal['directory'].startswith('.agent-changes-')
            or len(journal['directory']) != 47 or '/' in journal['directory']):
        raise ValueError('change_set_scene_invalid')
    with _observe_root(root_path) as root:
        if list(root.identity) != root_identity:
            raise ValueError('change_set_root_changed')
        fd = os.open(journal['directory'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root.descriptor)
        try:
            if identity(os.fstat(fd)) != journal['identity']:
                raise ValueError('change_set_scene_changed')
            yield root.descriptor, fd
            if identity(os.stat(journal['directory'], dir_fd=root.descriptor, follow_symlinks=False)) != journal['identity']:
                raise ValueError('change_set_scene_changed')
        finally:
            os.close(fd)


def apply_files(root_path, root_identity, entries, journal, checkpoint=lambda index: None):
    with scene(root_path, root_identity, journal) as (root, backup):
        if not journal['prepared']:
            raise ValueError('change_set_not_prepared')
        for item in entries:
            with parent_at(root, item['path']) as (parent, name):
                if not _matches(read_at(parent, name), item['before'], item['identity']):
                    raise ValueError('change_set_baseline_changed')
        for index, item in enumerate(entries):
            checkpoint(index)
            with parent_at(root, item['path']) as (parent, name):
                if not _matches(read_at(parent, name), item['before'], item['identity']):
                    raise ValueError('change_set_baseline_changed')
                if item['before'] is not None:
                    # 原inode移入私有现场，完整保留ACL/xattr/mode；恢复无需重建元数据。
                    if read_at(backup, f'old-{index}') is not None:
                        raise ValueError('change_set_scene_changed')
                    os.rename(name, f'old-{index}', src_dir_fd=parent, dst_dir_fd=backup)
                if item['after'] is not None:
                    if not _matches(read_at(backup, f'new-{index}'), item['after'], journal['new'][str(index)]):
                        raise ValueError('change_set_scene_changed')
                    # link拒绝覆盖后来出现的目录项；临时文件已经完整落盘。
                    os.link(f'new-{index}', name, src_dir_fd=backup, dst_dir_fd=parent, follow_symlinks=False)
                    os.unlink(f'new-{index}', dir_fd=backup)
                os.fsync(parent)
                os.fsync(backup)
        for index, item in enumerate(entries):
            if item['before'] is not None and not _matches(read_at(backup, f'old-{index}'), item['before'], item['identity']):
                raise ValueError('change_set_backup_changed')
            with parent_at(root, item['path']) as (parent, name):
                if not _matches(read_at(parent, name), item['after'], journal['new'].get(str(index))):
                    raise ValueError('change_set_postcheck_failed')


def restore_files(root_path, root_identity, entries, journal, *, require_applied=False, begin_restore=lambda: None):
    """先验证整组恢复条件；已恢复对象可跳过，外部内容或inode变化则不覆盖。"""
    if journal is None:
        observed_root, current = read_paths(root_path, [item['path'] for item in entries])
        if observed_root != root_identity or any(not _matches(current[item['path']], item['before'], item['identity']) for item in entries):
            raise ValueError('change_set_recovery_conflict')
        return
    with scene(root_path, root_identity, journal) as (root, backup):
        if not journal['prepared']:
            # 没有prepared确认就不会改动目标；保留现场，不猜测或递归删除它。
            for item in entries:
                with parent_at(root, item['path']) as (parent, name):
                    if not _matches(read_at(parent, name), item['before'], item['identity']):
                        raise ValueError('change_set_recovery_conflict')
            return
        if require_applied:
            for index, item in enumerate(entries):
                with parent_at(root, item['path']) as (parent, name):
                    if not _matches(read_at(parent, name), item['after'], journal['new'].get(str(index))):
                        raise ValueError('change_set_recovery_conflict')
        # link成功而unlink尚未完成时中断：只整理两个已登记的同inode别名。
        # 无关硬链接不视作本流程现场，不能借此解除普通文件的单链接约束。
        for index, item in enumerate(entries):
            with parent_at(root, item['path']) as (parent, name):
                current = read_at(parent, name, allow_pair=True)
                for prefix, expected_content, expected_identity in (
                    ('old', item['before'], item['identity']),
                    ('new', item['after'], journal['new'].get(str(index))),
                ):
                    duplicate = read_at(backup, f'{prefix}-{index}', allow_pair=True)
                    if (current is not None and duplicate is not None
                            and current == duplicate and _matches(current, expected_content, expected_identity)):
                        os.unlink(f'{prefix}-{index}', dir_fd=backup)
                        os.fsync(backup)
        def state(index, item):
            with parent_at(root, item['path']) as (parent, name):
                current = read_at(parent, name)
            original = read_at(backup, f'old-{index}')
            if _matches(current, item['before'], item['identity']) and original is None:
                return 'unchanged'
            if item['before'] is not None and not _matches(original, item['before'], item['identity']):
                raise ValueError('change_set_recovery_conflict')
            if current is not None and not _matches(current, item['after'], journal['new'].get(str(index))):
                raise ValueError('change_set_recovery_conflict')
            return 'restore'
        for index, item in enumerate(entries):
            state(index, item)
        begin_restore()
        for index in reversed(range(len(entries))):
            item = entries[index]
            if state(index, item) == 'unchanged':
                continue
            with parent_at(root, item['path']) as (parent, name):
                if read_at(parent, name) is not None:
                    os.unlink(name, dir_fd=parent)
                if item['before'] is not None:
                    os.link(f'old-{index}', name, src_dir_fd=backup, dst_dir_fd=parent, follow_symlinks=False)
                    os.unlink(f'old-{index}', dir_fd=backup)
                os.fsync(parent)
                os.fsync(backup)
