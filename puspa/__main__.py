"""Allow ``python -m puspa`` (the npm shim's fallback when the pip
console script is not on PATH)."""

from puspa.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
