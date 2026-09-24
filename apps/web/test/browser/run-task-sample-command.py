"""PC Task 样例命令专项：复用迁移、隔离数据库和真实 BFF 启动器。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[4]
OUTPUT = Path(tempfile.gettempdir()) / 'agent-task-sample-browser/output/playwright'
OUTPUT.mkdir(parents=True, exist_ok=True)
# 每次独立标记目录防止上次的就绪信号或报告造成假通过。
with tempfile.TemporaryDirectory(prefix='task-sample-browser-') as directory:
    paths = {
        'TASK_SAMPLE_FIXTURE': str(Path(directory) / 'fixture.json'),
        'TASK_SAMPLE_READY': str(Path(directory) / 'ready'),
        'TASK_SAMPLE_REPORT': str(Path(directory) / 'report.json'),
    }
    subprocess.run(
        [sys.executable, str(Path(__file__).with_name('run-isolated.py'))],
        cwd=ROOT, check=True, env=os.environ | paths | {
            'BROWSER_APP_MODE': 'local', 'BROWSER_TEST_SCRIPT': 'task-sample-command.mjs',
            'TASK_SAMPLE_OUTPUT': str(OUTPUT),
        },
    )
    report = json.loads(Path(paths['TASK_SAMPLE_REPORT']).read_text())
    assert report['all_containers_absent'] and report['task_sources_unchanged'] == 4
    (OUTPUT / 'server-evidence.json').write_text(json.dumps(report, indent=4))
    print('PASS server: four closed journals retained, original Task files unchanged, owned resources cleaned.')
