"""Probe readonly: exécutée par launchd pour vérifier son contexte de permission."""
import argparse
from pathlib import Path
import sys
from birthday_bot import save_state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        from ApplicationServices import AXIsProcessTrusted
        result = {"trusted": bool(AXIsProcessTrusted()), "python": str(Path(sys.executable).resolve())}
    except ImportError as exc:
        result = {"trusted": False, "error": str(exc)}
    save_state(args.output, result)
    return 0


if __name__ == "__main__":raise SystemExit(main())
