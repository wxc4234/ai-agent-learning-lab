"""纯参数构造的策略边界；不调用 Docker。"""

from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.command.command_environment import build_posix_command_environment
from app.services.runtime.sandbox.sandbox_spec import APPROVED_SANDBOX_IMAGE, build_sandbox_create_spec

TOKEN = "a" * 32


def build(request=None, **options):
    return build_sandbox_create_spec(
        request=request if request is not None else CommandRequest(argv=["/bin/echo", "hello"]),
        **({"execution_token": TOKEN} | options),
    )


def test_fixed_docker_policy_and_environment_boundaries():
    spec = build()
    image_index = spec.argv.index(APPROVED_SANDBOX_IMAGE)
    docker_options = spec.argv[2:image_index]
    assert spec.argv[:2] == ("docker", "create")
    assert spec.container_name == f"agent-sandbox-{TOKEN}"
    assert spec.execution_token == TOKEN
    expected = {
        f"--name={spec.container_name}", "--label=ai-agent-learning-lab.role=sandbox",
        f"--label=ai-agent-learning-lab.execution={TOKEN}",
        "--platform=linux/arm64", "--pull=never", "--init", "--user=10001:10001",
        "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
        "--cpus=0.5", "--memory=128m", "--memory-swap=128m", "--pids-limit=32", "--shm-size=8m",
        "--tmpfs=/home/agent:rw,noexec,nosuid,nodev,size=16m,mode=0700,uid=10001,gid=10001",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,mode=0700,uid=10001,gid=10001",
        "--workdir=/tmp", "--restart=no", "--stop-timeout=2", "--log-driver=none",
        "--entrypoint=/usr/bin/env",
    }
    # 精确集合加数量同时检查遗漏、重复和额外权限参数。
    assert set(docker_options) == expected
    assert len(docker_options) == len(expected)
    tail = spec.argv[image_index + 1:]
    assert tail[:2] == ("-i", "--")
    environment = build_posix_command_environment(home_directory="/home/agent", temporary_directory="/tmp")
    assert tail[2:13] == tuple(f"{key}={value}" for key, value in environment.items())
    assert tail[13:] == ("/bin/echo", "hello")


@pytest.mark.parametrize("argument", ["--privileged", "--network=host", "HOME=/root", "-S", "", "a b", "a\nb", '"quoted"', "$(echo unsafe)", ";echo unsafe"])
def test_command_arguments_cannot_enter_docker_or_env_options(argument):
    request = CommandRequest(argv=["/bin/echo", argument])
    spec = build(request)
    reference = build(CommandRequest(argv=["/bin/echo", "ordinary"]))
    assert spec.argv[:-1] == reference.argv[:-1]
    assert spec.argv[-1] == argument


@pytest.mark.parametrize("executable", ["echo", "./echo", "--help", "HOME=/root", "/tmp/HOME=x"])
def test_executable_cannot_be_env_option_or_assignment(executable):
    with pytest.raises(ValueError):
        build(CommandRequest(argv=[executable]))


@pytest.mark.parametrize("token", [None, True, 1, "", "a" * 31, "a" * 33, "A" * 32, "g" * 32, "a" * 32 + "\n", "--name=other"])
def test_invalid_execution_token(token):
    with pytest.raises(ValueError):
        build(execution_token=token)


@pytest.mark.parametrize("image", [None, True, "python:3.12-slim-bookworm", "python@sha256:" + "0" * 64, "other@sha256:" + "a" * 64, APPROVED_SANDBOX_IMAGE + "\n"])
def test_only_approved_digest_allowed(image):
    with pytest.raises(ValueError):
        build(image=image)


@pytest.mark.parametrize("directory", ["src", "./", "a/b"])
def test_unmounted_project_directories_rejected(directory):
    with pytest.raises(ValueError):
        build(CommandRequest(argv=["/bin/echo"], working_directory=directory))


@pytest.mark.parametrize("invalid_request", [None, {}, ["/bin/echo"], "/bin/echo"])
def test_wrong_request_type(invalid_request):
    with pytest.raises(TypeError):
        build_sandbox_create_spec(request=invalid_request, execution_token=TOKEN)


@pytest.mark.parametrize("mutation", ["empty", "nul", "oversize", "directory"])
def test_mutated_request_is_revalidated(mutation):
    request = CommandRequest(argv=["/bin/echo"])
    if mutation == "empty":
        request.argv.clear()
    elif mutation == "nul":
        request.argv.append("\x00")
    elif mutation == "oversize":
        request.argv.append("x" * 4097)
    else:
        request.working_directory = "../escape"
    with pytest.raises(ValidationError):
        build(request)


def test_snapshot_is_immutable_and_detached_from_request():
    request = CommandRequest(argv=["/bin/echo", "before"])
    spec = build(request)
    request.argv[-1] = "after"
    request.argv.append("--privileged")
    assert spec.argv[-2:] == ("/bin/echo", "before")
    assert isinstance(spec.argv, tuple)
    with pytest.raises(FrozenInstanceError):
        spec.container_name = "changed"
    other = build(execution_token="b" * 32)
    assert spec.container_name != other.container_name
    assert spec.argv == build(CommandRequest(argv=["/bin/echo", "before"])).argv
