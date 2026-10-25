import argparse
import json

from forge.wiring import build


def main() -> None:
    p = argparse.ArgumentParser(prog="forge")
    sub = p.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the web app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("bench", help="evaluate the seed playbook as v1 (if no versions exist)")
    sub.add_parser("nightly", help="propose -> probe -> compose over the playbook")
    v = sub.add_parser("verify", help="recompute a version hash and check it against the ledger")
    v.add_argument("version", type=int)
    sub.add_parser("models", help="list models on the configured OpenAI-compatible endpoint")
    sub.add_parser("smoke", help="fix one benchmark task with the configured LLM")
    args = p.parse_args()

    if args.cmd == "serve":
        import uvicorn

        from forge.web.app import create_app

        uvicorn.run(create_app(build()), host=args.host, port=args.port)
        return

    deps = build()
    if args.cmd == "bench":
        from forge.improve import ensure_seed

        out = ensure_seed(deps).model_dump(mode="json")
        out["playbook"] = "..."
    elif args.cmd == "nightly":
        from forge.improve import nightly

        out = nightly(deps)
    elif args.cmd == "verify":
        from forge.improve import verify

        out = verify(deps, args.version)
    elif args.cmd == "models":
        from openai import OpenAI

        client = OpenAI(base_url=deps.settings.llm_base_url, api_key=deps.settings.llm_api_key)
        out = sorted(m.id for m in client.models.list())
    else:  # smoke
        from forge.agent import load_task
        from forge.improve import playbook_hash
        from forge.models import Playbook

        pb = Playbook.model_validate_json(deps.settings.playbook_seed.read_text())
        pb.max_retries = 1
        run = deps.agent.solve(
            load_task(deps.settings.benchmarks_dir, "quicksort"), pb, playbook_hash(pb)
        )
        out = {
            "status": run.status,
            "tokens": run.tokens,
            "diff": run.attempts[-1].diff if run.attempts else None,
            "models": sorted({pr.model for pr in run.prompts}),
        }
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
