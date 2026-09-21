"""有界读取并校验 attach 升级响应，不创建或关闭连接。"""

import asyncio
import re


# 包含状态行、所有字段和末尾 CRLF CRLF。
# 这是项目接收策略，不是 HTTP 协议规定的通用上限。
MAX_ATTACH_HTTP_HEADER_BYTES = 8192

# 限制整段响应头的读取时间，不给每个字节重新计算预算。
ATTACH_HTTP_HEADER_TIMEOUT_SECONDS = 10

# 固定使用已核对 attach 协议的 API 版本。
# 不从模型参数或环境变量动态选择，也不在失败后自动降级重试。
# 本机 daemon 是否支持该版本，在连接接入课中单独核对。
ATTACH_DOCKER_API_VERSION = "1.45"

_HEADER_NAME = re.compile(rb"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_STATUS_LINE = re.compile(rb"HTTP/1\.1 101(?: [\x20-\x7e]*)?")

_MULTIPLEXED_CONTENT_TYPE = (
    b"application/vnd.docker.multiplexed-stream"
)


class DockerAttachHandshakeError(ValueError):
    """响应不符合当前 attach 握手策略。"""

    def __init__(self) -> None:
        # 不公开原始响应头、错误正文或内部连接信息。
        super().__init__("无法确认 Docker attach HTTP 握手成功")


def build_docker_attach_request(
    *,
    container_id: str,
) -> bytes:
    """构造固定 attach 请求；调用方仍负责容器归属与配置核对。"""

    # 仅接受完整小写 ID，不接受名称、短 ID、路径或查询字符串。
    # 不做 strip/lower 等修正，避免悄悄改变调用方指定的目标。
    if (
        not isinstance(container_id, str)
        or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
    ):
        raise ValueError("container_id 必须是完整小写容器 ID")

    # 查询选项由服务端固定，不开放 stdin 或历史日志读取。
    # stream=1 只是请求订阅，不能单凭请求字节证明订阅已经生效。
    target = (
        f"/v{ATTACH_DOCKER_API_VERSION}"
        f"/containers/{container_id}/attach"
        "?stream=1&logs=0&stdin=0&stdout=1&stderr=1"
    )

    # Host 是 HTTP/1.1 请求字段，不决定实际连接目标。
    # 后续连接层必须使用服务端固定的 Unix socket。
    # Content-Length: 0 明确本次请求没有正文。
    # 此处是请求头，与响应校验中拒绝 Content-Length 并不冲突。
    request = (
        f"POST {target} HTTP/1.1\r\n"
        "Host: docker\r\n"
        "Connection: Upgrade\r\n"
        "Upgrade: tcp\r\n"
        "Content-Length: 0\r\n"
        "\r\n"
    )

    # ID 已受限，其余内容均为服务端 ASCII 常量。
    # 返回不可变 bytes，发送层直接使用，不再追加模型提供的内容。
    return request.encode("ascii")


def _validate_upgrade_response(header: bytes) -> None:
    """校验完整响应头，不把服务端字段作为后续配置来源。"""

    if not header.endswith(b"\r\n\r\n"):
        raise DockerAttachHandshakeError()

    lines = header[:-4].split(b"\r\n")

    if not lines or _STATUS_LINE.fullmatch(lines[0]) is None:
        raise DockerAttachHandshakeError()

    fields: dict[bytes, bytes] = {}

    for line in lines[1:]:
        # 拒绝折叠字段、空字段名、字段名前后的空白和非法字符。
        name, separator, value = line.partition(b":")

        if not separator or _HEADER_NAME.fullmatch(name) is None:
            raise DockerAttachHandshakeError()

        # 当前接收策略只允许可打印 ASCII 和水平制表符。
        # 裸 CR/LF、NUL 等控制字节不能混入字段值。
        if any(
            byte != 9 and not 32 <= byte <= 126
            for byte in value
        ):
            raise DockerAttachHandshakeError()

        key = name.lower()

        # 不猜测重复字段的合并规则，避免关键协议字段含义不明确。
        if key in fields:
            raise DockerAttachHandshakeError()

        fields[key] = value.strip(b" \t")

    # 升级后的输出直接采用 Docker 帧格式。
    # 不在本层支持 HTTP chunked、压缩或普通响应体长度语义。
    if any(
        name in fields
        for name in (
            b"content-length",
            b"transfer-encoding",
            b"content-encoding",
        )
    ):
        raise DockerAttachHandshakeError()

    if fields.get(b"upgrade", b"").lower() != b"tcp":
        raise DockerAttachHandshakeError()

    # Connection 可以包含多个 token；字段名和 token 均不区分大小写。
    connection_tokens = [
        token.strip(b" \t").lower()
        for token in fields.get(b"connection", b"").split(b",")
    ]

    if any(
        _HEADER_NAME.fullmatch(token) is None
        for token in connection_tokens
    ):
        raise DockerAttachHandshakeError()

    if (
        b"upgrade" not in connection_tokens
        or b"close" in connection_tokens
    ):
        raise DockerAttachHandshakeError()

    content_type = fields.get(b"content-type", b"").lower()

    if content_type != _MULTIPLEXED_CONTENT_TYPE:
        raise DockerAttachHandshakeError()


async def read_docker_attach_upgrade(
    reader: asyncio.StreamReader,
) -> None:
    """消费并验证响应头，保留 reader 中尚未读取的输出帧。"""

    header = bytearray()

    try:
        # 一个总预算覆盖完整头部读取及校验。
        # 外部 CancelledError 不在这里捕获，仍交给连接拥有者处理。
        async with asyncio.timeout(
            ATTACH_HTTP_HEADER_TIMEOUT_SECONDS
        ):
            while len(header) < MAX_ATTACH_HTTP_HEADER_BYTES:
                # 只消费一个字节，保证不会越过头部结束位置。
                # StreamReader 自身会缓冲网络数据，这不等于每字节一次系统调用。
                chunk = await reader.read(1)

                if not isinstance(chunk, bytes):
                    raise TypeError("attach HTTP 读取必须返回 bytes")

                if len(chunk) > 1:
                    raise ValueError("attach HTTP 读取超过请求大小")

                if chunk == b"":
                    # 头部未完成时的 EOF 不能视为握手成功。
                    raise DockerAttachHandshakeError()

                header.extend(chunk)

                # 缓冲区已有数据时 read 可能立即返回。
                # 显式调度，使总超时和外部取消能够得到处理。
                await asyncio.sleep(0)

                if header.endswith(b"\r\n\r\n"):
                    _validate_upgrade_response(bytes(header))
                    return

            # 达到上限仍未遇到结束标记，立即拒绝，不继续排空。
            raise DockerAttachHandshakeError()

    except TimeoutError:
        # 转换为固定握手错误，不返回半段头部，也不继续解析帧。
        raise DockerAttachHandshakeError() from None
