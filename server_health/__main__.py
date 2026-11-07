import sys

from .cli import main


def run():
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(3)
    except Exception as exc:  # noqa: BLE001 - one readable line for cron, not a traceback
        print(f"server-health: error: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    run()
