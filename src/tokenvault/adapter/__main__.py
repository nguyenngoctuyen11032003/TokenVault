"""``python -m tokenvault.adapter`` (the portable / no-exe package entry point)."""
import sys

from tokenvault.adapter.main import main

if __name__ == "__main__":
    sys.exit(main())
