#!/bin/sh
# zen - упрощённый вход в zen.py (Termux / Linux / macOS)
DIR="$(cd "$(dirname "$0")" && pwd)"
case "$1" in
  ""|chat) shift 2>/dev/null; python3 "$DIR/zen.py" chat "$@" ;;
  serve)  shift; python3 "$DIR/zen.py" serve "$@" ;;
  models) python3 "$DIR/zen.py" models ;;
  *)      python3 "$DIR/zen.py" "$@" ;;
esac
