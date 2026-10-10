"""固定学习语料评测；默认预检/受控，真实发送必须双重显式开启。"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))


def main() -> int:
    try:
        from app.services.model.embedding_config import load_embedding_config
        from app.services.workspace.files.code_provider_evaluation import ProviderPlan
        from app.services.workspace.files.code_vector_evaluation import controlled_config
    except ValueError:
        print("配置加载失败；未发送请求。", file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(description="固定8段学习代码和8个问题；默认仅预检，不发送")
    parser.add_argument("--mode", choices=("controlled", "real"), default="controlled")
    parser.add_argument("--dataset", choices=("baseline", "distance-development", "distance-holdout"), default="baseline")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--response-model")
    parser.add_argument("--max-requests", type=int, default=9)
    parser.add_argument("--max-request-bytes", type=int, default=32768)
    parser.add_argument("--max-total-bytes", type=int, default=131072)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.mode == "real" and (not args.response_model or args.run and not args.allow_network):
        parser.error("真实模式需要--response-model；实际发送还需要--run --allow-network")
    if args.run and not args.output:
        parser.error("执行必须指定--output")
    try:
        config = load_embedding_config() if args.mode == "real" else controlled_config()
        limits = {"max_requests": args.max_requests, "max_request_bytes": args.max_request_bytes, "max_total_bytes": args.max_total_bytes}
        plan = ProviderPlan(config, args.response_model or config.model, dataset_role=args.dataset, **limits)
        manifest = plan.preflight()
    except ValueError:
        print("评测配置或预算预检失败；未发送请求。请核对独立Embedding配置与预算。", file=sys.stderr)
        return 2
    print(json.dumps({"mode": args.mode, "manifest": manifest}, ensure_ascii=False))
    if not args.run:
        return 0
    with TemporaryDirectory(prefix="provider-evaluation-") as directory:
        output = Path(directory) / "report.json"
        env = dict(os.environ, CODE_PROVIDER_COMMAND="run", CODE_PROVIDER_MODE=args.mode,
                   CODE_PROVIDER_RESPONSE_MODEL=plan.response_model, CODE_PROVIDER_DATASET=args.dataset, CODE_PROVIDER_LIMITS=json.dumps(limits),
                   CODE_PROVIDER_REPORT=str(output), CODE_PROVIDER_MANIFEST=json.dumps(manifest))
        # 隔离PG/目录由fixture管理；只在成功清理后发布报告。密钥不写参数或报告。
        result = subprocess.run([sys.executable, "-m", "pytest",
            "tests/workspace/files/test_code_provider_runner.py::test_provider_command",
            "-q", "--tb=no", "-W", "error"], cwd=ROOT / "apps/api", env=env, check=False)
        if result.returncode:
            print("评测失败；未发布报告，未重试。", file=sys.stderr)
            return result.returncode
        args.output.write_bytes(output.read_bytes())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
