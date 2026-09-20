"""本课真实 Docker 隔离专项；仅创建随机命名的临时 Compose 项目。"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "infra/sandbox/compose.yaml"

# 容器内检查实际生效的边界，不能仅凭 Compose 声明判定通过。
PROBE = r'''
import errno
import fcntl
import struct
import json
import os
from pathlib import Path
import socket
import sys

assert os.getuid() == os.getgid() == 10001
status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
assert int(status['CapEff'].strip(), 16) == 0
assert status['NoNewPrivs'].strip() == '1'
assert status['Seccomp'].strip() == '2'
assert os.statvfs('/').f_flag & os.ST_RDONLY
for directory in ['/home/agent', '/tmp']:
    target = Path(directory) / 'sandbox-probe.txt'
    target.write_text('temporary')
    assert target.read_text() == 'temporary'
    stat = os.statvfs(directory)
    assert stat.f_blocks * stat.f_frsize <= 16 * 1024 * 1024
    assert stat.f_flag & os.ST_NOSUID
    assert stat.f_flag & os.ST_NODEV
    assert stat.f_flag & os.ST_NOEXEC
    target.unlink()
try:
    Path('/etc/sandbox-probe.txt').write_text('forbidden')
except OSError as error:
    assert error.errno in (errno.EROFS, errno.EACCES)
else:
    raise AssertionError('root filesystem write succeeded')
assert not Path('/var/run/docker.sock').exists()
assert not Path(sys.argv[1]).exists()
# 通过当前进程的网络命名空间查询接口，不能用sysfs目录视图代替。
interfaces = sorted(name for _, name in socket.if_nameindex())
assert 'lo' in interfaces
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as query:
    for name in interfaces:
        flags = struct.unpack_from('H', fcntl.ioctl(query.fileno(), 0x8913, struct.pack('256s', name.encode())), 16)[0]
        assert name == 'lo' or not flags & 1, (name, flags)
print('Network interfaces (non-loopback must be down):', ', '.join(interfaces))
with socket.socket() as connection:
    connection.settimeout(1)
    try:
        connection.connect(('1.1.1.1', 443))
    except OSError as error:
        assert error.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH)
    else:
        raise AssertionError('external network reachable')
limits = Path('/sys/fs/cgroup')
assert (limits / 'memory.max').read_text().strip() == '134217728'
assert (limits / 'memory.swap.max').read_text().strip() == '0'
assert (limits / 'pids.max').read_text().strip() == '32'
quota, period = map(int, (limits / 'cpu.max').read_text().split())
assert quota / period == 0.5
# 检查真正的等待主进程环境，而非 docker inspect 中的镜像环境。
main_environment = None
for process in Path('/proc').iterdir():
    if not process.name.isdigit():
        continue
    try:
        arguments = (process / 'cmdline').read_bytes().split(b'\0')
        if b'import time; time.sleep(600)' in arguments:
            main_environment = dict(item.decode().split('=', 1) for item in (process / 'environ').read_bytes().split(b'\0') if item)
    except (FileNotFoundError, PermissionError):
        continue
assert main_environment is not None
expected = json.loads(sys.argv[2])
assert main_environment == expected, sorted(main_environment)
print('PASS uid/capabilities/seccomp, read-only root/tmpfs, host/socket absence, network, cgroup limits, main environment')
'''


def main():
    project = "sandbox-check-" + uuid4().hex[:12]
    image = os.environ.get("SANDBOX_IMAGE", "")
    if "@sha256:" not in image:
        raise RuntimeError("Set SANDBOX_IMAGE to a locally available image digest")
    environment = os.environ.copy()
    environment["SANDBOX_IMAGE"] = image
    # 此变量只在宿主Docker客户端环境中存在，不应出现在容器命令环境中。
    environment["SANDBOX_HOST_SENTINEL"] = "must-not-inherit"

    def docker(*arguments, check=True, timeout=30):
        result = subprocess.run(
            ["docker", *arguments], env=environment, text=True,
            capture_output=True, timeout=timeout, check=False,
        )
        if check and result.returncode:
            raise RuntimeError(result.stderr.strip() or "Docker command failed")
        return result

    def compose(*arguments):
        return docker("compose", "-p", project, "-f", str(COMPOSE), *arguments)

    config = json.loads(compose("config", "--format", "json").stdout)
    service = config["services"]["probe"]
    assert service["image"] == image
    with tempfile.TemporaryDirectory(prefix="sandbox-host-sentinel-") as directory:
        sentinel = Path(directory) / "outside.txt"
        sentinel.write_text("not mounted")
        try:
            compose("up", "-d")
            container = compose("ps", "-q", "probe").stdout.strip()
            assert container
            inspected = json.loads(docker("inspect", container).stdout)[0]
            host = inspected["HostConfig"]
            assert inspected["State"]["Running"]
            assert host["NetworkMode"] == "none" and not host["Privileged"]
            assert host["ReadonlyRootfs"] and host["Init"]
            assert host["LogConfig"]["Type"] == "none"
            assert not host.get("Binds")
            assert all(mount["Type"] == "tmpfs" for mount in inspected["Mounts"])
            assignments = service["command"][:11]
            expected = dict(item.split("=", 1) for item in assignments)
            assert set(expected) == {"PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"}
            output = docker("exec", container, "/usr/bin/env", "-i", *assignments,
                            "/usr/local/bin/python", "-c", PROBE, str(sentinel), json.dumps(expected))
            print(output.stdout.strip(), flush=True)

            # 两代进程忽略SIGTERM并单独建会话；必须由容器停止收尾。
            leaf = "import signal,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path('/tmp/grandchild-ready').touch(); time.sleep(600)"
            parent = (
                "import signal,subprocess,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                f"subprocess.Popen(['/usr/local/bin/python','-c',{leaf!r}],start_new_session=True); time.sleep(600)"
            )
            docker("exec", "-d", container, "/usr/bin/env", "-i", *assignments,
                   "/usr/local/bin/python", "-c", parent)
            for _ in range(40):
                if docker("exec", container, "/usr/bin/test", "-f", "/tmp/grandchild-ready", check=False).returncode == 0:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("descendant readiness timeout")
            assert len(docker("top", container, "-eo", "pid").stdout.splitlines()) >= 5
            compose("stop", "-t", "2", "probe")
            stopped = json.loads(docker("inspect", container).stdout)[0]["State"]
            assert not stopped["Running"] and stopped["Pid"] == 0
            assert stopped["FinishedAt"] != stopped["StartedAt"]
            assert docker("top", container, check=False).returncode != 0
            assert docker("exec", container, "/bin/true", check=False).returncode != 0
            print("PASS container stopped with detached descendants; daemon reports stopped/Pid=0 and rejects exec", flush=True)
            compose("start", "probe")
            assert docker("exec", container, "/usr/bin/test", "!", "-e", "/tmp/grandchild-ready").returncode == 0
            assert sentinel.read_text() == "not mounted"
            print("PASS tmpfs reset on restart; host sentinel unchanged", flush=True)
        finally:
            compose("down", "--timeout", "2")
            remaining = docker("ps", "-aq", "--filter", f"label=com.docker.compose.project={project}").stdout.strip()
            assert not remaining
            print("Temporary sandbox project cleaned", flush=True)


if __name__ == "__main__":
    main()
