"""Overstreet single-tenant entrypoint.

Wrapper mínimo para `python3 bot.py` — toda a lógica vive em overstreet.bot.
"""
import asyncio
import signal
import sys
from contextlib import suppress

from overstreet.bot import main as _main


def main() -> int:
    with suppress(KeyboardInterrupt):
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        return asyncio.run(_main())
    return 0


if __name__ == "__main__":
    sys.exit(main())
