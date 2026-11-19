"""PyInstaller entry point of the Adapter (windowed exe)."""
import sys

from tokenvault.adapter.main import main

if __name__ == "__main__":
    sys.exit(main())
