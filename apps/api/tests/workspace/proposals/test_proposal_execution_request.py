"""应用请求仅表达动作；不构成授权或文件执行能力。"""

import json

import pytest
from pydantic import ValidationError

from app.schemas import FileEditProposalExecutionRequest as Request


def test_explicit_action_round_trip_and_immutable():
    data = {'action': 'apply'}
    request = Request.model_validate(data)
    assert request.model_dump() == data
    assert Request.model_validate_json(request.model_dump_json()) == request
    with pytest.raises(ValidationError):
        request.action = 'apply'
    assert data == {'action': 'apply'}


@pytest.mark.parametrize('data', [None, [], 'apply', 1, True, {}])
def test_no_implicit_request(data):
    with pytest.raises(ValidationError):
        Request.model_validate(data)
    with pytest.raises(ValidationError):
        Request.model_validate_json(json.dumps(data))


@pytest.mark.parametrize('action', [None, False, True, 0, 1, [], {}, '',
                                    'Apply', ' apply', 'apply ', 'retry', 'force_apply'])
def test_action_is_exact_literal(action):
    with pytest.raises(ValidationError):
        Request.model_validate({'action': action})
    with pytest.raises(ValidationError):
        Request.model_validate_json(json.dumps({'action': action}))


@pytest.mark.parametrize('field', ['user_id', 'workspace_id', 'task_id', 'proposal_id',
                                   'path', 'content', 'baseline_sha256', 'application_token',
                                   'skip_baseline_check', 'is_sample'])
def test_extra_identity_content_or_capability_is_rejected(field):
    data = {'action': 'apply', field: 'PRIVATE'}
    with pytest.raises(ValidationError) as caught:
        Request.model_validate(data)
    assert caught.value.errors()[0]['type'] == 'extra_forbidden'
    with pytest.raises(ValidationError):
        Request.model_validate_json(json.dumps(data))


def test_bytes_are_not_coerced_to_action():
    with pytest.raises(ValidationError):
        Request.model_validate({'action': b'apply'})


def test_schema_documents_required_action_and_no_extra_fields():
    schema = Request.model_json_schema()
    assert schema['required'] == ['action']
    assert schema['additionalProperties'] is False
    assert schema['properties']['action']['const'] == 'apply'
    assert 'default' not in schema['properties']['action']
