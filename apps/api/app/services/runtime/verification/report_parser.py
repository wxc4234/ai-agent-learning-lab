"""解析独立报告流的唯一完整帧；不扫描测试日志或推断进程退出。"""

import json

from pydantic import ValidationError

from app.services.runtime.verification.contracts import VerificationTestReport

MAX_REPORT_BYTES = 4096


class VerificationReportError(ValueError):
    """只公开固定分类，不携带报告正文或测试中的私有信息。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def parse_verification_report(data: bytes, *, truncated: bool) -> VerificationTestReport:
    """只接受结束后取得的原始bytes；解析成功仍不证明进程成功或来源可信。"""
    if type(data) is not bytes or type(truncated) is not bool:
        raise VerificationReportError('verification_report_invalid_input')
    if truncated:
        raise VerificationReportError('verification_report_truncated')
    if len(data) > MAX_REPORT_BYTES:
        raise VerificationReportError('verification_report_limit')
    if not data:
        raise VerificationReportError('verification_report_missing')
    # 约定单行JSON加LF，额外帧/日志/半帧均拒绝，不能只取最后一段。
    if not data.endswith(b'\n') or data.count(b'\n') != 1:
        raise VerificationReportError('verification_report_invalid_format')
    try:
        text = data.decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        raise VerificationReportError('verification_report_invalid_encoding') from None
    try:
        value = json.loads(text, object_pairs_hook=_unique_object)
        if (type(value) is not dict or set(value) != {'version', 'complete', 'report'}
                or type(value['version']) is not int or value['version'] != 1
                or value['complete'] is not True):
            raise ValueError('invalid envelope')
    except (ValueError, RecursionError):
        raise VerificationReportError('verification_report_invalid_format') from None
    try:
        return VerificationTestReport.model_validate(value['report'])
    except ValidationError:
        raise VerificationReportError('verification_report_invalid_counts') from None
