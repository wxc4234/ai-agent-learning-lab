"""命令环境的精确白名单、路径语法与无宿主依赖边界。"""

import os
from pathlib import Path

import pytest

from app.services.runtime.command.command_environment import build_posix_command_environment


def build(**overrides):
    return build_posix_command_environment(**{
        "home_directory": "/sandbox/home",
        "temporary_directory": "/sandbox/tmp",
    } | overrides)


def test_exact_allowlist_and_target_paths():
    assert build() == {
        "PATH": "/usr/bin:/bin",
        "HOME": "/sandbox/home",
        "TMPDIR": "/sandbox/tmp",
        "TMP": "/sandbox/tmp",
        "TEMP": "/sandbox/tmp",
        "LANG": "C",
        "LC_ALL": "C",
        "XDG_CONFIG_HOME": "/sandbox/home/.config",
        "XDG_CACHE_HOME": "/sandbox/home/.cache",
        "XDG_DATA_HOME": "/sandbox/home/.local/share",
        "XDG_STATE_HOME": "/sandbox/home/.local/state",
    }


def test_host_secrets_and_injection_variables_are_not_inherited(monkeypatch):
    # 全部使用测试哨兵，不读取或打印真实宿主密钥。
    for key in [
        "OPENAI_API_KEY", "DATABASE_URL", "HTTPS_PROXY", "NODE_OPTIONS",
        "PYTHONPATH", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "BASH_ENV",
        "PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "XDG_CONFIG_HOME",
    ]:
        monkeypatch.setenv(key, "HOST_SENTINEL")
    environment = build()
    assert "HOST_SENTINEL" not in environment.values()
    assert not {"OPENAI_API_KEY", "DATABASE_URL", "HTTPS_PROXY", "NODE_OPTIONS",
                "PYTHONPATH", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "BASH_ENV"} & environment.keys()


def test_builder_does_not_read_host_environment(monkeypatch):
    class ForbiddenEnvironment:
        def __getitem__(self, key):
            raise AssertionError("must not inspect host environment")

        def __iter__(self):
            raise AssertionError("must not copy host environment")

        def get(self, *args):
            raise AssertionError("must not inspect host environment")

        def copy(self):
            raise AssertionError("must not copy host environment")

    with monkeypatch.context() as patch:
        patch.setattr(os, "environ", ForbiddenEnvironment())
        environment = build()
    assert environment["HOME"] == "/sandbox/home"


@pytest.mark.parametrize("field", ["home_directory", "temporary_directory"])
@pytest.mark.parametrize("value", [None, True, 1, b"/home", Path("/home")])
def test_directory_requires_string(field, value):
    with pytest.raises(TypeError):
        build(**{field: value})


@pytest.mark.parametrize("field", ["home_directory", "temporary_directory"])
@pytest.mark.parametrize("value", [
    "", "/", "//host/home", "///home", "relative", "C:/home", "C:\\home",
    "/a\\b", "/a\x00b", "/a\nb", "/a\tb", "/a\x7fb",
    "/a/../b", "/..", "/a//b", "/a/./b", "/a/", "/.", "/" + "a" * 4096,
])
def test_invalid_directory_syntax(field, value):
    with pytest.raises(ValueError):
        build(**{field: value})


@pytest.mark.parametrize("directory", ["/a", "/中文 目录", "/a..b", "/space ", "/" + "a" * 4095])
def test_valid_posix_spelling_is_preserved(directory):
    environment = build(home_directory=directory, temporary_directory=directory)
    assert environment["HOME"] == directory
    assert environment["TMPDIR"] == directory
    assert environment["XDG_CACHE_HOME"] == directory + "/.cache"


@pytest.mark.parametrize("field", ["env", "PATH", "user_id", "workspace_id"])
def test_arbitrary_overrides_are_not_accepted(field):
    with pytest.raises(TypeError):
        build(**{field: "unexpected"})


def test_each_call_returns_an_independent_dictionary():
    first = build()
    first["PATH"] = "/untrusted"
    first["EXTRA"] = "injected"
    second = build(home_directory="/other/home")
    assert second["PATH"] == "/usr/bin:/bin"
    assert "EXTRA" not in second
    assert second["XDG_CONFIG_HOME"] == "/other/home/.config"
    assert first["HOME"] == "/sandbox/home"


def test_build_does_not_resolve_create_or_inspect_directories(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("pure environment construction must not touch filesystem")

    for method in ["resolve", "mkdir", "stat", "exists", "is_dir", "open"]:
        monkeypatch.setattr(Path, method, forbidden)
    assert build(home_directory="/not-created/home")["HOME"] == "/not-created/home"
