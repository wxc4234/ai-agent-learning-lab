"""在自有项目副本中执行自定义命令，复用已验证容器生命周期与恢复记录。"""
import asyncio
from functools import partial
import json

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.sandbox.project_snapshot import create_project_snapshot
from app.services.runtime.sandbox.sandbox_sample_command import (
    _run_owned_sample_command, SampleCommandCancelled, SampleCommandUnconfirmed,
)
from app.services.runtime.sandbox.task_sample_command_journal import TaskSampleCommandJournal
from app.tools.context import ToolExecutionContext

# 可信固定启动器：载体由服务端生成，仅在容器/tmp解包，不在主机执行项目代码。
RUNNER = '''import base64,json,os,pathlib,sys
root=pathlib.Path('/tmp/project')
root.mkdir(mode=0o700)
for name,value in json.load(open('/workspace/example.txt')).items():
    path=root/name
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(base64.b64decode(value,validate=True))
request=json.loads(sys.argv[1])
os.chdir(root/request['working_directory'])
os.execv(request['argv'][0],request['argv'])
'''


def project_command_request(request: CommandRequest) -> CommandRequest:
    validated = CommandRequest.model_validate(request.model_dump())
    if not validated.argv[0].startswith('/') or '=' in validated.argv[0]:
        raise ValueError('command_request_rejected')
    return CommandRequest(argv=['/usr/local/bin/python', '-I', '-B', '-c', RUNNER,
                                json.dumps(validated.model_dump())])


async def run_project_command(*, request: CommandRequest, context: ToolExecutionContext,
                              journal: TaskSampleCommandJournal):
    """先登记包装后的真实argv；取消与未知均保存现场，不自动重试。"""
    journal.require_scope(user_id=context.user_id, conversation_id=context.conversation_id)
    wrapped = project_command_request(request)
    index = journal.reserve(wrapped)
    try:
        result = await _run_owned_sample_command(request=wrapped,
            prepare_sample=partial(create_project_snapshot, context=context), prepare_in_thread=True)
    except SampleCommandCancelled as error:
        journal.finish(index, status='cancelled', recovery=error.recovery)
        raise
    except SampleCommandUnconfirmed as error:
        journal.finish(index, status='unconfirmed', recovery=error.recovery)
        raise
    except asyncio.CancelledError:
        journal.finish(index, status='cancelled')
        raise
    except Exception:
        journal.finish(index, status='unconfirmed')
        raise
    journal.finish(index, status='completed', result=result)
    return result
