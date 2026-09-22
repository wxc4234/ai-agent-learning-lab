"""真实临时文件与受控故障验证替换边界；不接数据库或真实项目。"""

from dataclasses import FrozenInstanceError, asdict
from hashlib import sha256
import os
import stat
from types import SimpleNamespace

import pytest

from app.services.workspace.files import workspace_file_replace as service
from tests.workspace.metadata.metadata_support import plain_metadata
from tests.workspace.files.test_workspace_file import descriptors

__all__ = ['descriptors', 'plain_metadata']


@pytest.fixture
def sample(tmp_path, plain_metadata):
    root = tmp_path.resolve() / 'project'
    root.mkdir()
    folder = root / 'src'
    folder.mkdir()
    file = folder / '中文 file.txt'
    file.write_bytes(b'\xef\xbb\xbfold\r\n')
    file.chmod(0o640)
    return root, file


def replace(sample, **overrides):
    root, _ = sample
    args = {
        'bound_root': str(root), 'relative_path': 'src/中文 file.txt',
        'baseline_sha256': sha256(b'\xef\xbb\xbfold\r\n').hexdigest(),
        'proposed_content': '\ufeffnew\r\n',
        'proposed_sha256': sha256(b'\xef\xbb\xbfnew\r\n').hexdigest(),
    }
    return service.replace_workspace_text_file(**(args | overrides))


def temps(sample):
    return list(sample[0].rglob('.agent-edit-*.tmp'))


def patch_os(monkeypatch, name, replacement):
    original = getattr(os, name)
    monkeypatch.setattr(os, name, replacement)
    # OS包装仍使用真实系统调用，不能使能力检查误以为平台不支持。
    for capability in ('supports_dir_fd', 'supports_follow_symlinks'):
        if original in getattr(os, capability):
            monkeypatch.setattr(os, capability, getattr(os, capability) | {replacement})


@pytest.mark.parametrize('content', ['', '\ufeffnew\r\n', '中文\n', 'x' * (256 * 1024)])
def test_exact_content_metadata_immutable_and_clean(sample, descriptors, content):
    file = sample[1]
    before = file.stat()
    payload = content.encode()
    result = replace(sample, proposed_content=content, proposed_sha256=sha256(payload).hexdigest())
    assert result.status == 'replaced' and result.code == 'file_replaced'
    assert result.cleanup_complete and not temps(sample)
    assert file.read_bytes() == payload
    after = file.stat()
    assert (after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)) == (
        before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode))
    assert after.st_ino != before.st_ino
    assert set(asdict(result)) == {'status', 'code', 'cleanup_complete'}
    assert descriptors[0] and not descriptors[1]
    with pytest.raises(FrozenInstanceError):
        result.status = 'other'


@pytest.mark.parametrize('overrides', [
    {'bound_root': ''}, {'bound_root': '/'}, {'bound_root': 'relative'},
    {'bound_root': '/private/../tmp'}, {'bound_root': None},
    {'relative_path': '.'}, {'relative_path': '../outside'}, {'relative_path': '/tmp/file'},
    {'relative_path': 'C:drive'}, {'relative_path': 'a\\b'}, {'relative_path': None},
    {'baseline_sha256': 'BAD'}, {'proposed_sha256': 'BAD'},
    {'proposed_sha256': '0' * 64}, {'proposed_content': None},
    {'proposed_content': '\x00'}, {'proposed_content': '\ud800'},
    {'proposed_content': 'x' * (256 * 1024 + 1)}, {'proposed_content': '中' * 100000},
])
def test_invalid_input_never_opens_files(sample, monkeypatch, overrides):
    def forbidden(*args, **kwargs):
        pytest.fail('validation must precede filesystem open')

    patch_os(monkeypatch, 'open', forbidden)
    result = replace(sample, **overrides)
    assert result.status == 'not_replaced' and result.cleanup_complete


@pytest.mark.parametrize('kind', ['directory', 'fifo', 'symlink', 'hardlink', 'readonly', 'setuid', 'owner'])
def test_unsupported_target_preserved(sample, descriptors, monkeypatch, kind):
    root, file = sample
    if kind in ('directory', 'fifo', 'symlink'):
        file.unlink()
    if kind == 'directory':
        file.mkdir()
    elif kind == 'fifo':
        os.mkfifo(file)
    elif kind == 'symlink':
        outside = root.parent / 'outside'
        outside.write_text('PRIVATE')
        file.symlink_to(outside)
    elif kind == 'hardlink':
        os.link(file, root / 'other')
    elif kind == 'readonly':
        file.chmod(0o440)
    elif kind == 'setuid':
        file.chmod(0o4640)
        if not file.stat().st_mode & stat.S_ISUID:
            # 当前沙箱会清除setuid位；只在此平台分支注入元信息验证拒绝逻辑。
            original_stat = os.stat

            def special_bit(path, *args, **kwargs):
                value = original_stat(path, *args, **kwargs)
                if path == file.name and kwargs.get('dir_fd') is not None:
                    return SimpleNamespace(st_mode=value.st_mode | stat.S_ISUID,
                                           st_nlink=value.st_nlink, st_uid=value.st_uid)
                return value

            patch_os(monkeypatch, 'stat', special_bit)
    elif kind == 'owner':
        monkeypatch.setattr(os, 'geteuid', lambda: file.stat().st_uid + 1)
    result = replace(sample)
    assert result.status == 'not_replaced' and result.code == 'file_replace_target_unsupported'
    assert result.cleanup_complete and not temps(sample)
    if kind == 'symlink':
        assert file.is_symlink() and outside.read_text() == 'PRIVATE'


@pytest.mark.parametrize('kind', ['root', 'parent'])
def test_directory_links_rejected(sample, descriptors, kind):
    root, file = sample
    if kind == 'root':
        alias = root.parent / 'alias'
        alias.symlink_to(root, target_is_directory=True)
        result = replace(sample, bound_root=str(alias))
    else:
        alias = root / 'alias'
        alias.symlink_to(root / 'src', target_is_directory=True)
        result = replace(sample, relative_path='alias/中文 file.txt')
    assert result.status == 'not_replaced' and result.cleanup_complete
    assert file.read_bytes() == b'\xef\xbb\xbfold\r\n' and not temps(sample)


@pytest.mark.parametrize('data', [b'changed', b'\xef\xbb\xbfold\n', b'old\r\n', b'\xff', b'\x00', b'x' * (256 * 1024 + 1)])
def test_source_content_failure_keeps_original(sample, descriptors, data):
    sample[1].write_bytes(data)
    result = replace(sample)
    assert result.status == 'not_replaced'
    assert sample[1].read_bytes() == data and not temps(sample)


def test_missing_target_is_not_created(sample, descriptors):
    sample[1].unlink()
    assert replace(sample).status == 'not_replaced'
    assert not sample[1].exists() and not temps(sample)


def test_exclusive_temp_collision_does_not_delete_existing_file(sample, descriptors, monkeypatch):
    token = 'a' * 32
    occupied = sample[1].parent / f'.agent-edit-{token}.tmp'
    occupied.write_bytes(b'PRIVATE')
    monkeypatch.setattr(service, 'uuid4', lambda: SimpleNamespace(hex=token))
    result = replace(sample)
    assert result.status == 'not_replaced' and result.cleanup_complete
    assert occupied.read_bytes() == b'PRIVATE'
    assert sample[1].read_bytes() == b'\xef\xbb\xbfold\r\n'


@pytest.mark.parametrize('behavior', ['short', 'zero', 'raise'])
def test_write_boundary(sample, descriptors, monkeypatch, behavior):
    write = os.write

    def altered(fd, data):
        if behavior == 'zero':
            return 0
        if behavior == 'raise':
            raise OSError('PRIVATE write failure')
        return write(fd, data[:1])

    monkeypatch.setattr(os, 'write', altered)
    result = replace(sample)
    assert result.status == ('replaced' if behavior == 'short' else 'not_replaced')
    assert sample[1].read_bytes() == (b'\xef\xbb\xbfnew\r\n' if behavior == 'short' else b'\xef\xbb\xbfold\r\n')
    assert result.cleanup_complete and not temps(sample) and 'PRIVATE' not in repr(result)


@pytest.mark.parametrize('phase', ['file_sync', 'chmod', 'replace_before', 'replace_after', 'directory_sync', 'postcheck'])
def test_failure_classification_follows_replace_attempt(sample, descriptors, monkeypatch, phase):
    sync = os.fsync
    rename = os.replace
    reads = service._read_bounded_bytes
    renamed = False

    def fail(*args, **kwargs):
        raise OSError('PRIVATE fault')

    def sync_or_fail(fd):
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if (phase == 'directory_sync' and directory) or (phase == 'file_sync' and not directory):
            fail()
        return sync(fd)

    def rename_or_fail(*args, **kwargs):
        nonlocal renamed
        if phase == 'replace_before':
            fail()
        rename(*args, **kwargs)
        renamed = True
        if phase == 'replace_after':
            fail()

    def read_or_fail(fd):
        if renamed and phase == 'postcheck':
            fail()
        return reads(fd)

    monkeypatch.setattr(os, 'fsync', sync_or_fail)
    monkeypatch.setattr(os, 'replace', rename_or_fail)
    monkeypatch.setattr(service, '_read_bounded_bytes', read_or_fail)
    if phase == 'chmod':
        monkeypatch.setattr(os, 'fchmod', fail)
    result = replace(sample)
    assert result.status == ('not_replaced' if phase in ('file_sync', 'chmod') else 'uncertain')
    assert result.cleanup_complete and not temps(sample)
    assert sample[1].read_bytes() == (b'\xef\xbb\xbfnew\r\n' if renamed else b'\xef\xbb\xbfold\r\n')
    assert 'PRIVATE' not in repr(result)


@pytest.mark.parametrize('kind', ['edit', 'replace', 'parent_move', 'temp_swap'])
def test_observed_changes_before_replace_are_rejected(sample, descriptors, monkeypatch, kind):
    root, file = sample
    sync = os.fsync
    changed = False
    moved = root / 'moved'

    def mutate(fd):
        nonlocal changed
        sync(fd)
        if changed or stat.S_ISDIR(os.fstat(fd).st_mode):
            return
        changed = True
        if kind == 'edit':
            file.write_bytes(b'external edit')
        elif kind == 'replace':
            replacement = root / 'replacement'
            replacement.write_bytes(file.read_bytes())
            os.replace(replacement, file)
        elif kind == 'parent_move':
            file.parent.rename(moved)
            (root / 'src').mkdir()
        else:
            temp = temps(sample)[0]
            temp.unlink()
            temp.write_bytes(b'other owner')

    monkeypatch.setattr(os, 'fsync', mutate)
    result = replace(sample)
    assert result.status == 'not_replaced'
    assert result.cleanup_complete is (kind != 'temp_swap')
    if kind == 'temp_swap':
        assert temps(sample)[0].read_bytes() == b'other owner'
    else:
        assert not temps(sample)
    if kind == 'parent_move':
        assert (moved / file.name).read_bytes() == b'\xef\xbb\xbfold\r\n'
    else:
        assert file.read_bytes() == (b'external edit' if kind == 'edit' else b'\xef\xbb\xbfold\r\n')


def test_cleanup_failure_is_separate_from_target_outcome(sample, descriptors, monkeypatch):
    monkeypatch.setattr(os, 'write', lambda *args: 0)

    def fail(*args, **kwargs):
        raise PermissionError('PRIVATE cleanup')

    patch_os(monkeypatch, 'unlink', fail)
    result = replace(sample)
    assert result.status == 'not_replaced' and not result.cleanup_complete
    assert len(temps(sample)) == 1
    assert sample[1].read_bytes() == b'\xef\xbb\xbfold\r\n'


def test_close_failure_does_not_erase_success(sample, descriptors, monkeypatch):
    close = os.close
    raised = False

    def closed_then_error(fd):
        nonlocal raised
        close(fd)
        if not raised:
            raised = True
            raise OSError('PRIVATE close acknowledgement')

    monkeypatch.setattr(os, 'close', closed_then_error)
    result = replace(sample)
    assert result.status == 'replaced' and not result.cleanup_complete
    assert sample[1].read_bytes() == b'\xef\xbb\xbfnew\r\n'


def test_interrupt_cleans_resources_without_inventing_result(sample, descriptors, monkeypatch):
    def interrupt(*args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(os, 'write', interrupt)
    with pytest.raises(KeyboardInterrupt):
        replace(sample)
    assert not temps(sample) and sample[1].read_bytes() == b'\xef\xbb\xbfold\r\n'


def test_last_check_is_not_atomic_compare_and_swap(sample, descriptors, monkeypatch):
    # 可执行的限制证据：最后检查之后的外部编辑仍可能被replace覆盖。
    rename = os.replace

    def intervening_editor(*args, **kwargs):
        sample[1].write_bytes(b'external edit in final gap')
        return rename(*args, **kwargs)

    monkeypatch.setattr(os, 'replace', intervening_editor)
    result = replace(sample)
    assert result.status == 'replaced'
    assert sample[1].read_bytes() == b'\xef\xbb\xbfnew\r\n'


@pytest.mark.parametrize('capability', ['O_NOFOLLOW', 'fsync', 'dir_fd', 'follow_symlinks'])
def test_unsupported_platform_does_not_open_target(sample, monkeypatch, capability):
    if capability == 'dir_fd':
        monkeypatch.setattr(os, 'supports_dir_fd', set())
    elif capability == 'follow_symlinks':
        monkeypatch.setattr(os, 'supports_follow_symlinks', set())
    else:
        monkeypatch.delattr(os, capability)
    result = replace(sample)
    assert result.status == 'not_replaced' and result.code == 'file_replace_unsupported'
    assert sample[1].read_bytes() == b'\xef\xbb\xbfold\r\n' and not temps(sample)


@pytest.mark.parametrize('kind', ['file', 'fifo'])
def test_replacement_between_stat_and_open(sample, descriptors, monkeypatch, kind):
    file = sample[1]
    original_open = os.open
    changed = False

    def racing_open(path, flags, *args, **kwargs):
        nonlocal changed
        if path == file.name and not changed:
            changed = True
            file.unlink()
            if kind == 'fifo':
                os.mkfifo(file)
            else:
                file.write_bytes(b'external replacement')
        return original_open(path, flags, *args, **kwargs)

    patch_os(monkeypatch, 'open', racing_open)
    result = replace(sample)
    assert result.status == 'not_replaced' and not temps(sample)
    if kind == 'file':
        assert file.read_bytes() == b'external replacement'
    else:
        assert stat.S_ISFIFO(file.stat().st_mode)


def test_source_changes_during_read(sample, descriptors, monkeypatch):
    read = service._read_bounded_bytes
    changed = False

    def concurrent_write(fd):
        nonlocal changed
        data = read(fd)
        if not changed:
            changed = True
            sample[1].write_bytes(b'external write during read')
        return data

    monkeypatch.setattr(service, '_read_bounded_bytes', concurrent_write)
    result = replace(sample)
    assert result.status == 'not_replaced' and result.code == 'file_replace_target_changed'
    assert sample[1].read_bytes() == b'external write during read' and not temps(sample)


def test_staged_content_corruption_prevents_replace(sample, descriptors, monkeypatch):
    sync = os.fsync

    def corrupt(fd):
        sync(fd)
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, b'bad')

    monkeypatch.setattr(os, 'fsync', corrupt)
    result = replace(sample)
    assert result.status == 'not_replaced' and result.code == 'file_replace_staging_failed'
    assert sample[1].read_bytes() == b'\xef\xbb\xbfold\r\n' and not temps(sample)


@pytest.mark.parametrize('kind', ['target', 'directory'])
def test_post_replace_change_is_unknown_without_rollback(sample, descriptors, monkeypatch, kind):
    rename = os.replace
    root, file = sample
    moved = root / 'moved'

    def after(*args, **kwargs):
        rename(*args, **kwargs)
        if kind == 'target':
            file.unlink()
            file.write_bytes(b'external after replace')
        else:
            file.parent.rename(moved)
            file.parent.mkdir()

    monkeypatch.setattr(os, 'replace', after)
    result = replace(sample)
    assert result.status == 'uncertain' and result.cleanup_complete
    if kind == 'target':
        assert file.read_bytes() == b'external after replace'
    else:
        assert (moved / file.name).read_bytes() == b'\xef\xbb\xbfnew\r\n'
    assert not temps(sample)
