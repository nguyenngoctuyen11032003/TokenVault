"""``python -m tokenvault.manager`` (the portable / no-exe package entry point)."""
import sys

from tokenvault.manager.app import main

if __name__ == "__main__":
    sys.exit(main())
