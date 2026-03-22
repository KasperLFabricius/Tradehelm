"""Local launcher for TradeHelm."""
from __future__ import annotations

import argparse

import uvicorn

from tradehelm.control_api.app import create_app
from tradehelm.persistence.db import create_session_factory


def main() -> None:
    parser = argparse.ArgumentParser(description="Run TradeHelm control API locally")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db-url", default="sqlite:///tradehelm.db")
    args = parser.parse_args()

    create_session_factory(args.db_url)
    print(f"TradeHelm API starting on http://{args.host}:{args.port}")
    print("Then launch dashboard in another terminal:")
    print("  streamlit run src/tradehelm/dashboard/app.py")

    uvicorn.run(create_app(args.db_url), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
