"""Initialise the database and (optionally) load the demo data.

Usage:  python scripts/init_db.py            # tables + demo data
        python scripts/init_db.py --no-demo  # tables only
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from app.seed import seed_demo  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-demo", action="store_true", help="skip the demo user, sample database and chatbot")
    args = parser.parse_args()
    app = create_app()
    print("Database ready:", app.config["DATABASE_PATH"])
    if not args.no_demo:
        print(seed_demo(app))


if __name__ == "__main__":
    main()
