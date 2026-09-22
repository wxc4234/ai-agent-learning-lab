"""原生 ACL 故障注入代理；不再过滤任何扩展属性。"""

import pytest

from app.services.workspace.metadata import workspace_file_metadata as metadata


class NativeProxy:
    def __init__(self, native):
        self.native = native

    def __getattr__(self, name):
        return getattr(self.native, name)



@pytest.fixture
def plain_metadata(monkeypatch):
    native = metadata._native_library()
    proxy = NativeProxy(native)
    monkeypatch.setattr(metadata, '_native_library', lambda: proxy)
    return proxy
