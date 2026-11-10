import sys
from collections.abc import Sequence

from app.cli import lazyportfolio, macro, returns


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["macro"]:
        return macro.main(args[1:])
    if args[:1] == ["returns"]:
        return returns.main(args[1:])
    return lazyportfolio.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
