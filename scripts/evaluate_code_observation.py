"""隔离环境观测命令；测试清理成功后发布本轮测量，不覆盖固定质量预期。"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="离线检索耗时和用量；非真实模型性能或账单")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="code-observation-") as directory:
        report = Path(directory) / "report.json"
        result = subprocess.run([
            sys.executable, "-m", "pytest",
            "tests/workspace/files/test_code_evaluation_observation_pg.py::test_observed_pipeline[unknown]",
            "-q", "--tb=short", "-W", "error",
        ], cwd=ROOT / "apps/api", env=dict(os.environ, CODE_OBSERVATION_REPORT=str(report)), check=False)
        if result.returncode:
            return result.returncode
        value = json.loads(report.read_text())
        args.output.write_text(json.dumps(value, ensure_ascii=False, indent=4) + "\n")
        for row in value["latency"]:
            if row["stage"] == "end_to_end":
                print(f'{row["phase"]}/{row["status"]}: n={row["count"]}, p50={row["p50_ms"]:.3f}ms, p95={row["p95_ms"]:.3f}ms')
        print("模型用量和费用未知；本地受控HTTPX耗时不是供应商性能。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
