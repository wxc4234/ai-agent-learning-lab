"""报告流必须唯一、完整、有界；不在普通日志里搜索看似成功的片段。"""

import json

import pytest

from app.services.runtime.verification import report_parser as parser
from tests.runtime.verification.test_contracts import report


def frame(**changes):
    return (json.dumps({'version': 1, 'complete': True, 'report': report().model_dump(), **changes}) + chr(10)).encode()


def test_roundtrip_and_explicit_successful_methods():
    counts = parser.parse_verification_report(frame(), truncated=False)
    assert counts.successful_tests == 2 and counts.tests_run == 2


@pytest.mark.parametrize('data,truncated,code', [
    (b'', False, 'verification_report_missing'),
    (b'{}', False, 'verification_report_invalid_format'),
    (b'OK', False, 'verification_report_invalid_format'),
    (frame(), True, 'verification_report_truncated'),
    (b' ' * (parser.MAX_REPORT_BYTES + 1), False, 'verification_report_limit'),
    (bytes([255, 10]), False, 'verification_report_invalid_encoding'),
    (frame() + frame(), False, 'verification_report_invalid_format'),
    (b'PRIVATE' + frame(), False, 'verification_report_invalid_format'),
    (frame()[:-1], False, 'verification_report_invalid_format'),
    (None, False, 'verification_report_invalid_input'),
    (frame(), 0, 'verification_report_invalid_input'),
])
def test_rejects_bad_frames_without_echo(data, truncated, code):
    with pytest.raises(parser.VerificationReportError) as caught:
        parser.parse_verification_report(data, truncated=truncated)
    assert caught.value.code == code and 'PRIVATE' not in str(caught.value)


@pytest.mark.parametrize('changes', [
    {'version': True}, {'version': 2}, {'complete': False}, {'complete': 1},
    {'extra': 'PRIVATE'}, {'report': {}}, {'report': None},
    {'report': report().model_dump() | {'plan_id': 'other'}},
    {'report': report().model_dump() | {'successful_tests': 3}},
    {'report': report().model_dump() | {'tests_run': True}},
])
def test_bad_envelope_and_counts(changes):
    with pytest.raises(parser.VerificationReportError):
        parser.parse_verification_report(frame(**changes), truncated=False)


@pytest.mark.parametrize('data', [
    b'{"version":1,"version":1,"complete":true,"report":{}}',
    b'{"version":1,"complete":true,"report":{"plan_id":"a","plan_id":"b"}}',
    b'[]', b'null', b'{"version":1}', b'[' * 1200,
])
def test_duplicate_keys_wrong_root_and_deep_nesting(data):
    with pytest.raises(parser.VerificationReportError):
        parser.parse_verification_report(data + bytes([10]), truncated=False)


def test_all_prefixes_fail_until_complete_frame():
    data = frame()
    for end in range(len(data)):
        with pytest.raises(parser.VerificationReportError):
            parser.parse_verification_report(data[:end], truncated=False)


def test_exact_budget():
    data = frame()
    padded = b' ' * (parser.MAX_REPORT_BYTES - len(data)) + data
    assert parser.parse_verification_report(padded, truncated=False).successful_tests == 2
    with pytest.raises(parser.VerificationReportError, match='verification_report_limit'):
        parser.parse_verification_report(b' ' + padded, truncated=False)
