"""从固定开发集真实报告冻结规则；不联网、不读取留出、不覆盖既有文件。"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))


def main() -> int:
    from app.services.workspace.files.code_abstention_evaluation import Split
    from app.services.workspace.files.code_distance_calibration import calibrate_distance
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        split = Split.model_validate_json((ROOT / "apps/api/evaluations/code_retrieval/distance-v1/development.json").read_bytes())
        result = calibrate_distance(split, json.loads(args.report.read_text()))
        # 先完整校验再独占创建，不覆盖以前冻结的规则或未确认结果。
        with args.output.open("x", encoding="utf-8") as output:
            output.write(json.dumps(result, ensure_ascii=False, indent=4) + "\n")
    except (ValueError, KeyError, TypeError, OSError):
        print("校准失败：检查开发集/真实报告，输出必须为新文件。", file=sys.stderr)
        return 2
    print(json.dumps(result["selected"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
