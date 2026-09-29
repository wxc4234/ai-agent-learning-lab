"""对HEAD与index引用的叶对象验证封装、类型、长度与Git对象摘要。"""
import re
import zlib
from hashlib import sha1
from contextlib import ExitStack, contextmanager

from app.services.workspace.git.project_source import ProjectGitSourceError, _read_with_inspection
from app.services.workspace.git.project_staged import _observe_staged
from app.services.workspace.git.object_store import observe_object

MAX_OBJECT_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_OBJECTS = 256


def parse_blob(raw: bytes, oid: str) -> bytes:
    if type(raw) is not bytes or len(raw) > MAX_OBJECT_BYTES:
        raise ProjectGitSourceError('project_git_blob_limit')
    decoder = zlib.decompressobj()
    try:
        data = decoder.decompress(raw, MAX_OBJECT_BYTES + 1)
    except zlib.error:
        raise ProjectGitSourceError('project_git_blob_invalid') from None
    if len(data) > MAX_OBJECT_BYTES or decoder.unconsumed_tail:
        raise ProjectGitSourceError('project_git_blob_limit')
    header, separator, body = data.partition(b'\0')
    if (not decoder.eof or decoder.unused_data or not separator
            or re.fullmatch(rb'blob (0|[1-9][0-9]{0,7})', header) is None
            or int(header[5:]) != len(body) or sha1(data).hexdigest() != oid):
        raise ProjectGitSourceError('project_git_blob_invalid')
    return body


@contextmanager
def _observe_verified(root: int):
    with _observe_staged(root) as staged, ExitStack() as stack:
        graph, index, reasons, changes = staged
        if reasons:
            raise ProjectGitSourceError('project_git_objects_unavailable')
        config, _, _, _, _, leaves, _ = graph
        assert leaves is not None and index is not None
        objects = {}
        total = 0
        for entry in (*leaves, *index.entries):
            # Gitlink引用外部仓库，不能假称对象已在当前仓库验证。
            if entry.mode == 0o160000:
                raise ProjectGitSourceError('project_git_gitlink_unsupported')
            if entry.object_id in objects:
                continue
            if len(objects) >= MAX_OBJECTS:
                raise ProjectGitSourceError('project_git_blob_limit')
            raw = stack.enter_context(observe_object(config, entry.object_id, max_bytes=MAX_OBJECT_BYTES))
            if raw is None:
                raise ProjectGitSourceError('project_git_blob_missing')
            body = parse_blob(raw, entry.object_id)
            total += len(body)
            if total > MAX_TOTAL_BYTES:
                raise ProjectGitSourceError('project_git_blob_limit')
            objects[entry.object_id] = body
        # 外层第二次授权后仍保留全部叶对象观察器，用于退出复核。
        yield {'objects': objects, 'head': leaves, 'index': index.entries, 'changes': changes}


def read_verified_project_objects(request: object):
    """仅交付完整通过的独立字节；不把结果作为后续写入授权。"""
    return _read_with_inspection(request, _observe_verified)
