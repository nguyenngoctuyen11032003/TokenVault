"""PyInstaller entry point of the Manager (windowed exe)."""
import sys

from tokenvault.manager.app import main

if __name__ == "__main__":
    sys.exit(main())
