import argparse
import json
from typing import Any

from scout.wiring import Deps, build


def _units(deps: Deps, usdc: float) -> int:
    return round(usdc * 10**deps.settings.usdc_decimals)


def main() -> None:
    p = argparse.ArgumentParser(prog="scout")
    sub = p.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the web app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("index", help="nightly index build (Algorithm 1)")
    m = sub.add_parser("match", help="match a GitHub user to contributions (Algorithm 2)")
    m.add_argument("login")
    m.add_argument("--skills", default="", help="extra self-reported skills, comma-separated")
    f = sub.add_parser("fund", help="fund a bounty as the demo funder (test USDC)")
    f.add_argument("repo")
    f.add_argument("issue", type=int)
    f.add_argument("usdc", type=float)
    pr = sub.add_parser("pr-merged", help="handle a merged PR, as the webhook would")
    pr.add_argument("repo")
    pr.add_argument("number", type=int)
    sub.add_parser("tick", help="poll for merged PRs and settle due bounties")
    d = sub.add_parser("dispute", help="dispute a verified bounty as the demo funder")
    d.add_argument("bounty_id")
    d.add_argument("--reason", default="disputed from the CLI")
    sub.add_parser("bounties", help="list bounties")
    ni = sub.add_parser("nightly", help="nightly self-improvement by backtest (Algorithm 4)")
    ni.add_argument(
        "--refresh-backtest",
        action="store_true",
        help="rebuild the frozen backtest set (scores reset to a new baseline)",
    )
    v = sub.add_parser("verify", help="check a playbook version hash against the ledger")
    v.add_argument("version", type=int)
    sub.add_parser("models", help="list models on the configured LLM endpoint")
    sub.add_parser("solana-setup", help="devnet wallets, SOL for fees, test USDC mint")
    wl = sub.add_parser("wallet", help="register a contributor's payout address")
    wl.add_argument("login")
    wl.add_argument("address")
    args = p.parse_args()

    if args.cmd in ("solana-setup", "wallet"):  # must work before the Solana ledger exists
        from scout.adapters.cloud.solana_setup import register_wallet, setup
        from scout.config import Settings

        settings = Settings()
        if args.cmd == "wallet":
            register_wallet(settings, args.login, args.address)
            print(json.dumps({"registered": args.login, "address": args.address}))
        else:
            print(json.dumps(setup(settings), indent=2))
        return

    if args.cmd == "serve":
        import uvicorn

        from scout.web.app import create_app

        uvicorn.run(create_app(build()), host=args.host, port=args.port)
        return

    deps = build()
    out: Any
    if args.cmd == "index":
        from scout.index import build_index
        from scout.playbook import current_playbook

        snap = build_index(deps, current_playbook(deps)[0])
        out = {
            "repos": [r.full_name for r in snap.repos],
            "gaps": [g.id for g in snap.gaps],
            "skipped_repos": snap.skipped_repos,
            "dropped_issues": snap.dropped_issues,
            "tool_status": snap.tool_status,
            "mode": snap.mode,
        }
    elif args.cmd == "match":
        from scout.match import match

        skills = [s.strip() for s in args.skills.split(",") if s.strip()]
        r = match(deps, args.login, skills)
        out = {
            "profile": r.profile.model_dump(),
            "trace": f"traces/match-{r.id}.json",
            "matches": [
                {
                    "gap": m.gap.id,
                    "score": m.score,
                    "parts": m.parts,
                    "funded_units": m.funded_units,
                    "verified": m.verification.ok if m.verification else None,
                    "verification": m.verification.detail if m.verification else None,
                    "plan": m.plan,
                }
                for m in r.matches
            ],
        }
    elif args.cmd in ("fund", "pr-merged", "tick", "dispute", "bounties"):
        from scout.bounty import Bounties

        bs = Bounties(deps)
        if args.cmd == "fund":
            out = bs.fund(
                args.repo, args.issue, deps.ledger.funder_account(), _units(deps, args.usdc)
            ).model_dump(mode="json")
        elif args.cmd == "pr-merged":
            out = [b.model_dump(mode="json") for b in bs.on_pr_merged(args.repo, args.number)]
        elif args.cmd == "tick":
            out = bs.tick()
        elif args.cmd == "dispute":
            out = bs.dispute(args.bounty_id, deps.ledger.funder_account(), args.reason).model_dump(
                mode="json"
            )
        else:
            out = [b.model_dump(mode="json") for b in bs.all()]
    elif args.cmd == "nightly":
        from scout.improve import nightly

        out = nightly(deps, refresh_backtest=args.refresh_backtest)
    elif args.cmd == "verify":
        from scout.improve import verify

        out = verify(deps, args.version)
    else:
        out = deps.llm.list_models()
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
