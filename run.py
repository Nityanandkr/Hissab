"""Start Hisaab:   python run.py        ->  http://127.0.0.1:5056

Your data lives in ./data (SQLite file + model files) and never leaves this computer.
Options (environment variables):  PORT=5056   HISAAB_DATA=/some/folder   HISAAB_LLM=anthropic (optional)
"""
from __future__ import annotations

import logging
import os

from hisaab import config
from hisaab.web.app import create_app


def main() -> None:
    quiet = os.environ.get("HISAAB_QUIET") == "1"                 # set by launch.py: no request log lines at all (they would list the pages you open)
    logging.basicConfig(level=logging.WARNING if quiet else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if quiet:
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
    config.ensure_dirs()
    app = create_app()
    print(f"\n  Hisaab is running →  http://{config.HOST}:{config.PORT}\n  (only this computer can open it; press Ctrl+C to stop)\n")
    app.run(host=config.HOST, port=config.PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
