"""调用隔离PG评测夹具；测试库/项目归夹具所有，成功收尾后才发布报告。"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="固定v1受控向量与词面评测；需本机PostgreSQL测试权限，无真实模型请求")
    parser.add_argument("--output", type=Path, help="成功后保存JSON报告；省略时仅打印比较摘要")
    parser.add_argument("--fusion", action="store_true", help="增加来源级RRF（常量60）的三策略比较")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="code-vector-evaluation-") as directory:
        report = Path(directory) / "report.json"
        env = dict(os.environ, CODE_VECTOR_EVALUATION_REPORT=str(report))
        # pytest返回前fixture已释放连接、删除独立schema和随机测试库。
        result = subprocess.run([
            sys.executable, "-m", "pytest",
            ("tests/workspace/files/test_code_rrf_comparison.py::test_three_strategy_comparison" if args.fusion else
             "tests/workspace/files/test_code_vector_evaluation.py::test_real_pipeline_comparison_and_no_mutation"),
            "-q", "--tb=short", "-W", "error",
        ], cwd=ROOT / "apps/api", env=env, check=False)
        if result.returncode:
            return result.returncode
        value = json.loads(report.read_text(encoding="utf-8"))
        if args.output:
            args.output.write_text(json.dumps(value, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
        for name in (("literal", "controlled_vector", "rrf") if args.fusion else ("literal", "controlled_vector")):
            row = value[name]
            print(f'{name}: Recall@3={row["recall_at_k"]:.4f}, MRR@3={row["mrr_at_k"]:.4f}, no-answer false positives={row["no_answer_false_positive_count"]}/2')
        print("受控特征向量仅验证链路，不代表真实模型语义；报告未包含真实延迟或成本。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
