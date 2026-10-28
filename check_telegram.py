"""Standalone Telegram check. Network access requires the explicit --send flag."""

from news_super_analyser.telegram_notify_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
