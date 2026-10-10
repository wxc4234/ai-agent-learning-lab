"""离线应用已冻结规则；不搜索阈值、不覆写规则或既有输出。"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))


def main() -> int:
    from app.services.workspace.files.code_distance_holdout import load_frozen_holdout, evaluate_holdout
    from app.services.workspace.files.code_retrieval_evaluation import load_dataset
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    directory = ROOT / "apps/api/evaluations/code_retrieval"
    try:
        baseline, _ = load_dataset(directory / "v1")
        holdout, policy = load_frozen_holdout(directory / "distance-v1", baseline)
        result = evaluate_holdout(holdout, json.loads(args.report.read_text()), policy)
        with args.output.open("x", encoding="utf-8") as output:
            output.write(json.dumps(result, ensure_ascii=False, indent=4) + "\n")
    except (ValueError, KeyError, TypeError, OSError):
        print("留出评估失败：核对冻结绑定与真实报告；输出必须是新文件。", file=sys.stderr)
        return 2
    print(json.dumps(result["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
