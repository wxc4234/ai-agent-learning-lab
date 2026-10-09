"""独立开发/留出两阶段入口；隔离测试收尾成功后才发布报告。"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="受控检索拒答离线实验；不代表生产语义效果")
    parser.add_argument("phase", choices=("calibrate", "evaluate"))
    parser.add_argument("--policy", type=Path, required=True, help="校准阶段新建冻结文件；评估阶段只读加载")
    parser.add_argument("--output", type=Path, required=True, help="完整本轮报告")
    args = parser.parse_args()
    if args.policy.resolve() == args.output.resolve():
        parser.error("策略文件与报告文件不能相同")
    if args.phase == "calibrate" and args.policy.exists():
        parser.error("冻结文件已存在；请使用新路径，不覆盖已冻结策略")
    if args.phase == "evaluate" and not args.policy.is_file():
        parser.error("必须先提供已冻结的策略文件")
    with TemporaryDirectory(prefix="code-abstention-") as directory:
        report = Path(directory) / "report.json"
        env = dict(os.environ, CODE_ABSTENTION_REPORT=str(report))
        if args.phase == "evaluate":
            env["CODE_ABSTENTION_POLICY"] = str(args.policy.resolve())
        case = "test_development_calibration" if args.phase == "calibrate" else "test_frozen_holdout_evaluation"
        result = subprocess.run([sys.executable, "-m", "pytest", f"tests/workspace/files/test_code_abstention_comparison.py::{case}", "-q", "--tb=short", "-W", "error"], cwd=ROOT / "apps/api", env=env, check=False)
        if result.returncode:
            return result.returncode
        value = json.loads(report.read_text(encoding="utf-8"))
        if args.phase == "calibrate":
            # 排他创建，禁止重复命令悄悄覆盖已冻结参数。策略和报告不是共同事务。
            with args.policy.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(value["policy"], ensure_ascii=False, indent=4) + "\n")
        args.output.write_text(json.dumps(value, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
        print(f'{args.phase}: threshold={value["policy"]["min_shared_terms"]}, Recall@3={value["after"]["recall_at_k"]}, false refusals={value["after"]["abstention"]["false_refusal_count"]}, false acceptances={value["after"]["abstention"]["false_acceptance_count"]}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
