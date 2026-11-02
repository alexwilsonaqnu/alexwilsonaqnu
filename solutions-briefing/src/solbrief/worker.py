"""Worker process (Fly process group `worker`): executes pipeline stages and reply-to-query jobs."""

from __future__ import annotations

import logging

from solbrief import telemetry


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    telemetry.configure("worker")
    from solbrief.queue import app

    app.run_worker(queues=["pipeline", "reply", "email"], concurrency=4, install_signal_handlers=True)


if __name__ == "__main__":
    main()
