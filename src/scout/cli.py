import argparse
import json
from typing import Any

from scout.wiring import build


def main() -> None:
    p = argparse.ArgumentParser(prog="scout")
    sub = p.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the web app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("models", help="list models on the configured LLM endpoint")
    args = p.parse_args()

    if args.cmd == "serve":
        import uvicorn

        from scout.web.app import create_app

        uvicorn.run(create_app(build()), host=args.host, port=args.port)
        return

    deps = build()
    out: Any = deps.llm.list_models()
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
