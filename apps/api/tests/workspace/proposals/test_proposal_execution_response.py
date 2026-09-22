"""公开执行契约的完整状态空间、严格投影和无副作用边界。"""

from dataclasses import replace
from itertools import product
import traceback

import pytest
from pydantic import ValidationError

from app.routers.workspace import proposal_execution_response as boundary
from app.schemas import FileEditProposalExecutionResponse as Response
from app.services.workspace.proposals import file_edit_proposal_execution as execution

PREFIX = 'proposal_application_'
# 业务允许的17种回执：8种已确认终态、8种登记未确认、1种领取未确认。
VALID = [
    ('not_attempted', None, 'not_applied', 'not_applied'),
    ('not_replaced', True, 'not_applied', 'not_applied'),
    ('not_replaced', False, 'uncertain', 'uncertain'),
    ('replaced', True, 'applied', 'applied'),
    ('replaced', False, 'uncertain', 'uncertain'),
    ('uncertain', None, 'uncertain', 'uncertain'),
    ('uncertain', True, 'uncertain', 'uncertain'),
    ('uncertain', False, 'uncertain', 'uncertain'),
    ('not_attempted', None, 'unknown', 'registration_unconfirmed'),
    ('not_replaced', True, 'unknown', 'registration_unconfirmed'),
    ('not_replaced', False, 'unknown', 'registration_unconfirmed'),
    ('replaced', True, 'unknown', 'registration_unconfirmed'),
    ('replaced', False, 'unknown', 'registration_unconfirmed'),
    ('uncertain', None, 'unknown', 'registration_unconfirmed'),
    ('uncertain', True, 'unknown', 'registration_unconfirmed'),
    ('uncertain', False, 'unknown', 'registration_unconfirmed'),
    ('not_attempted', None, 'unknown', 'claim_unconfirmed'),
]
IDS = {'workspace_id': 'a' * 32, 'task_id': 'b' * 32, 'proposal_id': 'c' * 32}


def payload(case=VALID[3]):
    file_status, clean, status, code = case
    return dict(IDS, file_status=file_status, cleanup_complete=clean,
                application_status=status, code=PREFIX + code)


def internal(data):
    return execution.ProposalExecutionResult(**{
        key: data[key] for key in ('proposal_id', 'file_status',
                                  'application_status', 'code', 'cleanup_complete')
    })


def build(data):
    return boundary.build_proposal_execution_response(
        **{key: data[key] for key in IDS}, result=internal(data),
    )


@pytest.mark.parametrize('case', VALID)
def test_allowed_cases_round_trip_without_losing_evidence(case):
    data = payload(case)
    response = build(data)
    assert response.model_dump() == data
    assert Response.model_validate_json(response.model_dump_json()) == response
    with pytest.raises(ValidationError):
        response.cleanup_complete = True


def test_entire_declared_state_space_rejects_all_other_combinations():
    # 穷举字段声明内的240种组合，防止独立字段合法却组合矛盾。
    cases = product(('not_attempted', 'not_replaced', 'replaced', 'uncertain'),
                    (None, True, False),
                    ('applied', 'not_applied', 'uncertain', 'unknown'),
                    ('applied', 'not_applied', 'uncertain',
                     'claim_unconfirmed', 'registration_unconfirmed'))
    accepted = rejected = 0
    for case in cases:
        data = payload(case)
        if case in VALID:
            assert build(data).model_dump() == data
            accepted += 1
        else:
            with pytest.raises(ValidationError):
                Response.model_validate(data)
            with pytest.raises(boundary.ProposalExecutionResponseError):
                build(data)
            rejected += 1
    assert (accepted, rejected) == (17, 223)


@pytest.mark.parametrize('key,value', [
    ('cleanup_complete', 0), ('cleanup_complete', 1),
    ('cleanup_complete', 'true'), ('cleanup_complete', 'false'),
    ('cleanup_complete', 1.0), ('cleanup_complete', []),
    ('file_status', 'PRIVATE-status'), ('file_status', None),
    ('application_status', 'running'), ('application_status', 'idle'),
    ('application_status', None), ('code', 'PRIVATE-code'), ('code', None),
    ('workspace_id', 123), ('workspace_id', b'a' * 32),
    ('workspace_id', 'A' * 32), ('workspace_id', 'a' * 31),
    ('task_id', 'b' * 33), ('task_id', 'b' * 31 + '\n'),
    ('proposal_id', ''), ('proposal_id', None), ('proposal_id', 'g' * 32),
])
def test_strict_inputs_fail_safely(key, value):
    data = payload()
    data[key] = value
    with pytest.raises(boundary.ProposalExecutionResponseError) as caught:
        build(data)
    assert str(caught.value) == 'proposal_execution_response_invalid'
    assert 'PRIVATE' not in ''.join(traceback.format_exception(caught.value))


@pytest.mark.parametrize('key', tuple(payload()))
def test_every_field_required(key):
    data = payload()
    del data[key]
    with pytest.raises(ValidationError):
        Response.model_validate(data)


@pytest.mark.parametrize('key', ['application_token', 'bound_root', 'proposed_content'])
def test_schema_forbids_extra_private_fields(key):
    with pytest.raises(ValidationError):
        Response.model_validate(dict(payload(), **{key: 'PRIVATE'}))


def test_internal_extra_fields_are_not_projected():
    result = internal(payload())
    # 模拟内部对象将来增加字段；公开投影不能随之扩大。
    object.__setattr__(result, 'application_token', 'PRIVATE-token')
    object.__setattr__(result, 'bound_root', '/PRIVATE/path')
    object.__setattr__(result, 'proposed_content', 'PRIVATE-content')
    response = boundary.build_proposal_execution_response(**IDS, result=result)
    assert response.model_dump() == payload()
    assert 'PRIVATE' not in response.model_dump_json()


def test_mismatched_receipt_id_is_not_overwritten():
    result = replace(internal(payload()), proposal_id='d' * 32)
    with pytest.raises(boundary.ProposalExecutionResponseError):
        boundary.build_proposal_execution_response(**IDS, result=result)


@pytest.mark.parametrize('kind', ['none', 'dict', 'object', 'subclass'])
def test_non_exact_internal_objects_rejected(kind):
    class Child(execution.ProposalExecutionResult):
        pass
    candidates = {'none': None, 'dict': payload(), 'object': object(),
                  'subclass': Child('c' * 32, 'replaced', 'applied', PREFIX + 'applied', True)}
    with pytest.raises(boundary.ProposalExecutionResponseError):
        boundary.build_proposal_execution_response(**IDS, result=candidates[kind])


def test_no_execution_database_or_file_operations(monkeypatch):
    import builtins
    import os
    from app.services.workspace.proposals import file_edit_proposal_application_query as query
    def forbidden(*args, **kwargs):
        pytest.fail('response projection must have no I/O')
    for name in ('SessionLocal', 'execute_task_file_edit_proposal',
                 'claim_task_file_edit_proposal', 'finish_task_file_edit_proposal',
                 'replace_workspace_text_file', 'check_task_file_edit_proposal'):
        monkeypatch.setattr(execution, name, forbidden)
    monkeypatch.setattr(query, 'SessionLocal', forbidden)
    monkeypatch.setattr(query, 'get_task_file_edit_proposal_application_status', forbidden)
    # 限定补丁作用域，避免影响pytest自身的文件读写和报告。
    with monkeypatch.context() as guard:
        guard.setattr(builtins, 'open', forbidden)
        guard.setattr(os, 'open', forbidden)
        guard.setattr(os, 'replace', forbidden)
        for case in VALID:
            assert build(payload(case)).model_dump() == payload(case)
        with pytest.raises(boundary.ProposalExecutionResponseError):
            build(dict(payload(), code='PRIVATE'))
