"""样例挂载专项：真实临时文件与响应篡改，不接触业务数据库。"""

import copy
import json
import os
from dataclasses import replace

import pytest

from app.services.runtime.sandbox import sandbox_sample as samples
from app.services.runtime.sandbox.sandbox_isolation_policy import (
    SandboxIsolationPolicyError,
    confirm_sandbox_isolation_policy,
)
from app.services.runtime.sandbox.sandbox_spec import (
    APPROVED_SANDBOX_IMAGE,
    build_sample_sandbox_create_spec,
    build_sandbox_create_spec,
)
from tests.runtime.sandbox.test_sandbox_creation import CID, TOKEN, request
from tests.runtime.sandbox.test_sandbox_isolation_policy import fixture as base_payload


@pytest.fixture
def sample_base(tmp_path, monkeypatch):
    # 测试只在本用例的私有目录建样例，不扫描或清理开发样例。
    base = tmp_path.resolve()
    monkeypatch.setattr(samples.tempfile, "gettempdir", lambda: str(base))
    before = dict(samples._ACTIVE_SAMPLES)
    yield base
    # 故障用例可能有意留下部分清理状态，由 pytest 的私有 tmp_path 回收。
    for token in set(samples._ACTIVE_SAMPLES) - set(before):
        del samples._ACTIVE_SAMPLES[token]


@pytest.fixture
def sample(sample_base):
    value = samples.create_sandbox_sample()
    yield value
    if samples._ACTIVE_SAMPLES.get(value.token) is value:
        samples.cleanup_sandbox_sample(value)


def mounted_payload(sample):
    data = base_payload()
    data[0]["HostConfig"]["Mounts"] = [{
        "Type": "bind", "Source": str(sample.root), "Target": "/workspace",
        "ReadOnly": True,
        "BindOptions": {"Propagation": "rprivate", "NonRecursive": True},
    }]
    data[0]["Mounts"] = [{
        "Type": "bind", "Source": str(sample.root), "Destination": "/workspace",
        "RW": False, "Propagation": "rprivate", "Mode": "ro",
    }]
    return data


def confirm(sample, data):
    return confirm_sandbox_isolation_policy(
        request=request(), execution_token=TOKEN, container_id=CID,
        inspect_stdout=json.dumps(data), sample=sample,
    )


def test_factory_spec_policy_and_cleanup(sample):
    path = sample.root / samples.SAMPLE_FILENAME
    before = (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
    assert before[0] == samples.SAMPLE_CONTENT
    assert samples.confirm_sandbox_sample_source(sample) == str(sample.root)
    assert sample.root.parent.stat().st_mode & 0o777 == 0o700
    assert sample.root.stat().st_mode & 0o777 == 0o755
    assert path.stat().st_mode & 0o777 == 0o666
    base = build_sandbox_create_spec(request=request(), execution_token=TOKEN)
    spec = build_sample_sandbox_create_spec(request=request(), execution_token=TOKEN, sample=sample)
    mounts = [arg for arg in spec.argv if arg.startswith("--mount=")]
    assert mounts == [
        (f"--mount=type=bind,source={sample.root},target=/workspace,readonly,"
         "bind-propagation=rprivate,bind-recursive=disabled")
    ]
    assert spec.argv.index(mounts[0]) < spec.argv.index(APPROVED_SANDBOX_IMAGE)
    assert tuple(arg for arg in spec.argv if arg != mounts[0]) == base.argv
    data = mounted_payload(sample)
    original = copy.deepcopy(data)
    assert confirm(sample, data).container_id == CID
    assert data == original
    assert (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns) == before
    samples.cleanup_sandbox_sample(sample)
    assert not sample.root.parent.exists()
    with pytest.raises(samples.SandboxSampleError):
        samples.confirm_sandbox_sample_source(sample)


def test_forged_equal_object_and_unregistered_object_rejected(sample):
    for forged in (replace(sample), replace(sample, token="0" * 32), None):
        with pytest.raises(samples.SandboxSampleError):
            samples.confirm_sandbox_sample_source(forged)
    assert samples.confirm_sandbox_sample_source(sample) == str(sample.root)


@pytest.mark.parametrize("level", ["root", "parent"])
@pytest.mark.parametrize("replacement", ["directory", "symlink"])
def test_directory_or_symlink_replacement_rejected(sample, level, replacement):
    target = sample.root if level == "root" else sample.root.parent
    saved = target.with_name(target.name + "-original")
    target.rename(saved)
    try:
        if replacement == "symlink":
            target.symlink_to(saved, target_is_directory=True)
        else:
            target.mkdir()
        for operation in (
            lambda: samples.confirm_sandbox_sample_source(sample),
            lambda: samples.cleanup_sandbox_sample(sample),
            lambda: build_sample_sandbox_create_spec(
                request=request(), execution_token=TOKEN, sample=sample,
            ),
            lambda: confirm(sample, mounted_payload(sample)),
        ):
            with pytest.raises(samples.SandboxSampleError):
                operation()
        assert saved.exists()
    finally:
        if target.is_symlink():
            target.unlink()
        else:
            target.rmdir()
        saved.rename(target)


@pytest.mark.parametrize("change", ["replace", "symlink", "hardlink", "extra_file", "extra_parent", "mode_root", "mode_parent", "mode_file"])
def test_file_identity_entries_and_permissions_rejected(sample, change):
    path = sample.root / samples.SAMPLE_FILENAME
    saved = sample.root.parent.parent / (sample.token + "-original")
    extra = sample.root / "extra"
    original_mode = None
    target = None
    try:
        if change in ("replace", "symlink"):
            path.rename(saved)
            if change == "replace":
                path.write_bytes(samples.SAMPLE_CONTENT)
                path.chmod(0o666)
            else:
                path.symlink_to(saved)
        elif change == "hardlink":
            os.link(path, saved)
        elif change.startswith("extra"):
            extra = (sample.root if change == "extra_file" else sample.root.parent) / "extra"
            extra.write_text("must remain")
        else:
            target = {"mode_root": sample.root, "mode_parent": sample.root.parent, "mode_file": path}[change]
            original_mode = target.stat().st_mode & 0o777
            target.chmod(0o755 if change == "mode_parent" else 0o700)
        with pytest.raises(samples.SandboxSampleError):
            samples.confirm_sandbox_sample_source(sample)
        with pytest.raises(samples.SandboxSampleError):
            samples.cleanup_sandbox_sample(sample)
        assert path.exists()
    finally:
        if change in ("replace", "symlink"):
            path.unlink()
            saved.rename(path)
        elif change == "hardlink":
            saved.unlink()
        elif change.startswith("extra"):
            extra.unlink()
        else:
            target.chmod(original_mode)


def test_source_unavailable_and_cleanup_failure_preserve_registration(sample, monkeypatch):
    original = samples.os.unlink
    def failed_unlink(*args, **kwargs):
        raise PermissionError("private detail")
    with monkeypatch.context() as patch:
        patch.setattr(samples.os, "unlink", failed_unlink)
        with pytest.raises(samples.SandboxSampleError) as caught:
            samples.cleanup_sandbox_sample(sample)
        assert "private detail" not in str(caught.value)
    assert samples.os.unlink is original
    assert samples._ACTIVE_SAMPLES[sample.token] is sample
    assert (sample.root / samples.SAMPLE_FILENAME).read_bytes() == samples.SAMPLE_CONTENT


@pytest.mark.parametrize("stage", ["mkdir", "open_root", "write", "zero_write"])
def test_creation_failure_closes_descriptors_and_retains_only_own_partial_site(sample_base, monkeypatch, stage):
    real_open, real_write, real_mkdir = os.open, os.write, os.mkdir
    descriptors = []
    def tracked_open(path, *args, **kwargs):
        if stage == "open_root" and path == "repository":
            raise OSError("private")
        fd = real_open(path, *args, **kwargs)
        descriptors.append(fd)
        return fd
    def injected_write(fd, data):
        if stage == "write":
            raise OSError("private")
        if stage == "zero_write":
            return 0
        return real_write(fd, data)
    def injected_mkdir(path, *args, **kwargs):
        if stage == "mkdir":
            raise OSError("private")
        return real_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(samples.os, "open", tracked_open)
    monkeypatch.setattr(samples.os, "write", injected_write)
    monkeypatch.setattr(samples.os, "mkdir", injected_mkdir)
    before = dict(samples._ACTIVE_SAMPLES)
    with pytest.raises(samples.SandboxSampleError):
        samples.create_sandbox_sample()
    assert samples._ACTIVE_SAMPLES == before
    for fd in set(descriptors):
        with pytest.raises(OSError):
            os.fstat(fd)


def test_short_writes_complete_content(sample_base, monkeypatch):
    original = os.write
    monkeypatch.setattr(samples.os, "write", lambda fd, data: original(fd, data[:2]))
    value = samples.create_sandbox_sample()
    try:
        assert (value.root / samples.SAMPLE_FILENAME).read_bytes() == samples.SAMPLE_CONTENT
    finally:
        samples.cleanup_sandbox_sample(value)


def test_unsupported_platform_and_mount_delimiters(sample_base, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(samples.sys, "platform", "win32")
        with pytest.raises(samples.SandboxSampleError):
            samples.create_sandbox_sample()
    unsafe = sample_base / "unsafe,source"
    unsafe.mkdir()
    monkeypatch.setattr(samples.tempfile, "gettempdir", lambda: str(unsafe))
    with pytest.raises(samples.SandboxSampleError):
        samples.create_sandbox_sample()
    assert list(unsafe.iterdir()) == []


@pytest.mark.parametrize("location,field,value", [
    ("host", "Type", "volume"), ("host", "Source", "/wrong"),
    ("host", "Target", "/tmp"), ("host", "ReadOnly", False),
    ("host", "ReadOnly", 1), ("host", "BindOptions", None),
    ("host", "VolumeOptions", {}),
    ("options", "Propagation", "rshared"), ("options", "NonRecursive", False),
    ("options", "NonRecursive", 1), ("options", "Unknown", False),
    ("options", "ReadOnlyNonRecursive", True),
    ("options", "ReadOnlyForceRecursive", True),
    ("options", "ReadOnlyForceRecursive", 0),
    ("reported", "Type", "volume"), ("reported", "Source", "/wrong"),
    ("reported", "Destination", "/etc"), ("reported", "RW", True),
    ("reported", "RW", 0), ("reported", "Propagation", "rshared"),
    ("config", "WorkingDir", "/workspace"), ("config", "User", "0:0"),
    ("isolation", "NetworkMode", "host"), ("isolation", "Privileged", True),
    ("isolation", "ReadonlyRootfs", False), ("isolation", "Memory", 0),
    ("isolation", "Binds", ["/wrong:/extra:ro"]),
    ("isolation", "VolumesFrom", ["other"]),
])
def test_mounted_policy_rejects_changed_boundaries(sample, location, field, value):
    data = mounted_payload(sample)
    target = {
        "host": data[0]["HostConfig"]["Mounts"][0],
        "options": data[0]["HostConfig"]["Mounts"][0]["BindOptions"],
        "reported": data[0]["Mounts"][0],
        "config": data[0]["Config"], "isolation": data[0]["HostConfig"],
    }[location]
    target[field] = value
    with pytest.raises(SandboxIsolationPolicyError):
        confirm(sample, data)


@pytest.mark.parametrize("location", ["host", "reported"])
@pytest.mark.parametrize("change", ["missing", "duplicate", "extra", "null", "wrong_type", "bad_entry"])
def test_mount_list_is_exact(sample, location, change):
    data = mounted_payload(sample)
    target = data[0]["HostConfig"] if location == "host" else data[0]
    original = copy.deepcopy(target["Mounts"][0])
    target["Mounts"] = {
        "missing": [], "duplicate": [original, original],
        "extra": [original, {"Type": "bind", "Source": "/other", "Destination": "/extra"}],
        "null": None, "wrong_type": {}, "bad_entry": [None],
    }[change]
    with pytest.raises(SandboxIsolationPolicyError):
        confirm(sample, data)


@pytest.mark.parametrize("location,fields", [
    ("host", ("Type", "Source", "Target", "ReadOnly", "BindOptions")),
    ("options", ("Propagation", "NonRecursive")),
    ("reported", ("Type", "Source", "Destination", "RW", "Propagation")),
])
def test_missing_mount_fields_rejected(sample, location, fields):
    for field in fields:
        data = mounted_payload(sample)
        target = {
            "host": data[0]["HostConfig"]["Mounts"][0],
            "options": data[0]["HostConfig"]["Mounts"][0]["BindOptions"],
            "reported": data[0]["Mounts"][0],
        }[location]
        del target[field]
        with pytest.raises(SandboxIsolationPolicyError):
            confirm(sample, data)


def test_explicit_false_options_and_tmpfs_summary_allowed(sample):
    data = mounted_payload(sample)
    data[0]["HostConfig"]["Mounts"][0]["BindOptions"].update({
        "ReadOnlyNonRecursive": False, "ReadOnlyForceRecursive": False,
    })
    data[0]["Mounts"].extend([
        {"Type": "tmpfs", "Destination": dest, "RW": True}
        for dest in ("/tmp", "/home/agent")
    ])
    assert confirm(sample, data).container_id == CID
    with pytest.raises(SandboxIsolationPolicyError):
        confirm(None, data)


def test_sample_branch_still_rejects_identity_and_nonstandard_json(sample):
    data = mounted_payload(sample)
    data[0]["Id"] = "f" * 64
    with pytest.raises(SandboxIsolationPolicyError):
        confirm(sample, data)
    for text in ('[{"Id":"a","Id":"b"}]', '[{"x":NaN}]', 'private'):
        with pytest.raises(SandboxIsolationPolicyError):
            confirm_sandbox_isolation_policy(
                request=request(), execution_token=TOKEN, container_id=CID,
                inspect_stdout=text, sample=sample,
            )
