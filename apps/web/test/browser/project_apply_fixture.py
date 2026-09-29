"""普通项目PC写入后独立核对真实数据库、文件和审计。"""
import json
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import FileEditProposal, ProposalAuditEvent
from project_write_grant_fixture import OUTPUT


def verify(engine):
    item = json.loads((OUTPUT / 'fixtures.json').read_text())[0]
    assert Path(item['file']).read_bytes() == b'old\n'
    with Session(engine) as session:
        proposal = session.scalar(select(FileEditProposal).where(FileEditProposal.external_id == item['proposal_id']))
        assert proposal is not None and proposal.application_status == 'applied'
        assert proposal.baseline_content == 'old\n'
        events = session.scalars(select(ProposalAuditEvent.event).where(
            ProposalAuditEvent.proposal_id == proposal.id).order_by(ProposalAuditEvent.id)).all()
        assert events[:4] == ['approved', 'grant_issued', 'application_started', 'applied']
        assert events[4].startswith('restore:')
        reverse = session.scalar(select(FileEditProposal).where(FileEditProposal.external_id == events[4][8:]))
        assert reverse is not None and reverse.application_status == 'applied'
        assert reverse.baseline_content == 'new\n' and reverse.proposed_content == 'old\n'
    (OUTPUT / 'apply-database.json').write_text(json.dumps({'applied': True, 'backup': True, 'events': events}))
