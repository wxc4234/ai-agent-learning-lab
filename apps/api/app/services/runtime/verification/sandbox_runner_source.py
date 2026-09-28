"""固定容器启动代码：只读单文件快照转换为临时测试模块。"""

from app.services.runtime.verification.runner_source import UNITTEST_RUNNER_SOURCE

# 快照工厂只接受一个固定普通文件。暂不扩大宿主目录结构或挂载权限；
# 测试包在容器tmpfs中创建，容器删除时一并回收，不修改来源。
_BOOTSTRAP = r'''
import pathlib
import tempfile
_verification_root = tempfile.mkdtemp(prefix="verification-", dir="/tmp")
_tests = pathlib.Path(_verification_root) / "tests"
_tests.mkdir()
(_tests / "__init__.py").write_bytes(b"")
with open("/workspace/example.txt", "rb") as _source:
    _content = _source.read(262145)
if len(_content) > 262144:
    raise RuntimeError("snapshot limit")
(_tests / "test_target.py").write_bytes(_content)
'''.strip()

SANDBOX_UNITTEST_SOURCE = _BOOTSTRAP + '\n' + UNITTEST_RUNNER_SOURCE.replace(
    'ROOT = "/workspace"', 'ROOT = _verification_root',
)
