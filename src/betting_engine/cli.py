import argparse
import json
from pathlib import Path
from .library import ingest, search
from .quant import Probabilities, decimal_payoffs, expected_return, paper_stake


def main():
    parser = argparse.ArgumentParser(description="Betting engine research foundation (not live)")
    sub = parser.add_subparsers(dest="command", required=True)
    book = sub.add_parser("ingest", help="Extract/index local books; does not mark them read")
    book.add_argument("source", type=Path)
    book.add_argument("--output", type=Path, default=Path("knowledge"))
    find = sub.add_parser("search")
    find.add_argument("query")
    find.add_argument("--database", type=Path, default=Path("knowledge/library.sqlite"))
    sub.add_parser("demo", help="Synthetic arithmetic example; no actual recommendations")
    args = parser.parse_args()
    try:
        if args.command == "ingest":
            result = ingest(args.source, args.output)
            print(json.dumps({"files": len(result["files"]), "read": 0,
                              "failed": sum(r["status"] == "extraction_failed" for r in result["files"])}, indent=2))
        elif args.command == "search":
            print(json.dumps(search(args.database, args.query), ensure_ascii=False, indent=2))
        else:
            p = Probabilities(.56, 0, .44)
            cautious = p.conservative(.03)
            payoffs = decimal_payoffs(2.05)
            print(json.dumps({"mode": "SYNTHETIC_DEMO_NOT_A_BET", "model_fitted": False,
                "assumed_probability": p.win, "sensitivity_probability": cautious.win,
                "assumed_odds": 2.05, "expected_return": expected_return(p, payoffs),
                "sensitivity_return": expected_return(cautious, payoffs),
                "paper_stake_usd": paper_stake(cautious, payoffs, bankroll=200,
                    daily_remaining=10, event_remaining=2)}, indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Cannot continue: {exc}\n")


if __name__ == "__main__":
    main()
