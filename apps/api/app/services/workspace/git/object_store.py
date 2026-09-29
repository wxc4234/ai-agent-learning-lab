"""同作用域loose/pack对象读取；pack索引不参与授权或内容判断。"""
import os
import re
import zlib
from contextlib import ExitStack, contextmanager

from app.services.workspace.git.pack_objects import MAX_PACK_BYTES, PackError, PackedObject, parse_pack
from app.services.workspace.git.project_source import ProjectGitSourceError

MAX_PACKS = 4
MAX_PACK_TOTAL = 32 * 1024 * 1024
MAX_STORE_BYTES = 8 * 1024 * 1024
MAX_STORE_OBJECTS = 4000


class GitObjectStore:
    def __init__(self, directory: int, stack: ExitStack):
        self.directory = directory
        self.stack = stack
        self.loaded = False
        self.objects: dict[str, PackedObject] = {}

    def packed(self, oid: str) -> bytes | None:
        if not self.loaded:
            self._load()
        item = self.objects.get(oid)
        if item is None:
            return None
        return zlib.compress(f'{item.kind} {len(item.body)}'.encode() + b'\0' + item.body)

    def _load(self):
        from app.services.workspace.git.project_head import _observe_file
        # 目录描述符与所有pack文件留到外层第二次授权后；布局观察器复核命名关系。
        try:
            parent = os.open('objects', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.directory)
            self.stack.callback(os.close, parent)
            directory = os.open('pack', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            self.stack.callback(os.close, directory)
        except FileNotFoundError:
            self.loaded = True
            return
        names = []
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name.endswith('.pack'):
                    if not re.fullmatch(r'pack-[0-9a-f]{40}\.pack', entry.name):
                        raise ProjectGitSourceError('git_pack_invalid')
                    names.append(entry.name)
                    if len(names) > MAX_PACKS:
                        raise ProjectGitSourceError('git_pack_limit')
        total = expanded = 0
        for name in sorted(names):
            raw = self.stack.enter_context(_observe_file(directory, (name,), max_bytes=min(MAX_PACK_BYTES, MAX_PACK_TOTAL - total)))
            if raw is None or raw[-20:].hex() != name[5:-5]:
                raise ProjectGitSourceError('git_pack_invalid')
            total += len(raw)
            try:
                objects = parse_pack(raw)
            except PackError as exc:
                raise ProjectGitSourceError(exc.code) from None
            expanded += sum(len(item.body) for item in objects)
            if expanded > MAX_STORE_BYTES or len(self.objects) + len(objects) > MAX_STORE_OBJECTS:
                raise ProjectGitSourceError('git_pack_limit')
            for obj in objects:
                previous = self.objects.get(obj.object_id)
                if previous is not None and previous != obj:
                    raise ProjectGitSourceError('git_pack_invalid')
                self.objects[obj.object_id] = obj
        self.loaded = True


@contextmanager
def observe_object(config, oid: str, *, max_bytes: int):
    from app.services.workspace.git.project_head import _observe_file
    if re.fullmatch(r'[0-9a-f]{40}', oid) is None:
        raise ProjectGitSourceError('git_pack_invalid')
    with _observe_file(config.descriptor, ('objects', oid[:2], oid[2:]), missing_allowed=True, max_bytes=max_bytes) as raw:
        yield raw if raw is not None else config.object_store.packed(oid)
