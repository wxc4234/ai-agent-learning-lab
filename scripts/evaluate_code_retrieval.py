"""根目录运行：.venv/bin/python scripts/evaluate_code_retrieval.py --k 3。"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.services.workspace.files.code_retrieval_evaluation import (
    Run,
    evaluate,
    lexical_baseline,
    load_dataset,
    read_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="离线代码来源评测；不连接模型、数据库或产品项目")
    parser.add_argument("--dataset", type=Path, default=ROOT / "apps/api/evaluations/code_retrieval/v1")
    parser.add_argument("--predictions", type=Path, help="Run格式JSON；省略时运行透明词面基线")
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--output", type=Path, help="写入报告，省略则输出JSON到stdout")
    args = parser.parse_args()
    try:
        dataset, texts = load_dataset(args.dataset)
        run = Run.model_validate(read_json(args.predictions)) if args.predictions else lexical_baseline(dataset, texts)
        report = evaluate(dataset, run, k=args.k)
        rendered = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=4) + "\n"
        # 只有全部校验/评分成功才输出，不生成部分成功报告；没有数据库事务。
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    except (ValueError, OSError, SyntaxError) as error:
        parser.exit(2, f"评测失败：{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
