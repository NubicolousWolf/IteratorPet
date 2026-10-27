"""Read-only probe for a local Rain World installation.

Prints object counts and a name inventory. It never writes extracted assets to
disk; this tool exists so the adapter can be written against real resource keys
instead of guessed ones.
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
from pathlib import Path

KEYWORDS = ("iterator", "pebble", "moon", "puppet", "tether", "cable", "arm")


def iter_objects(path: Path):
    import UnityPy

    env = UnityPy.load(str(path))
    for obj in env.objects:
        yield obj


def probe(path: Path, pattern: str, limit: int) -> int:
    counts: collections.Counter[str] = collections.Counter()
    names: dict[str, list[str]] = collections.defaultdict(list)
    for obj in iter_objects(path):
        type_name = obj.type.name
        counts[type_name] += 1
        try:
            name = obj.peek_name()
        except Exception:
            name = None
        if name:
            names[type_name].append(name)

    print(f"file: {path}")
    print(f"size: {path.stat().st_size}")
    print("type counts:")
    for type_name, count in counts.most_common():
        print(f"  {type_name:24s} {count}")

    rx = re.compile(pattern, re.IGNORECASE)
    print(f"\nnames matching /{pattern}/:")
    total = 0
    for type_name in ("Texture2D", "Sprite", "TextAsset", "MonoBehaviour", "Material", "Shader"):
        for name in sorted(set(names.get(type_name, ()))):
            if rx.search(name):
                print(f"  {type_name:14s} {name}")
                total += 1
                if total >= limit:
                    print("  ... (limit reached)")
                    return total
    print(f"  ({total} matches)")
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=r"F:\steam\steamapps\common\Rain World",
        help="Rain World installation root",
    )
    parser.add_argument("--pattern", default="|".join(KEYWORDS))
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--all", action="store_true", help="dump every name of the file")
    args = parser.parse_args(argv)

    root = Path(args.root)
    assets = root / "RainWorld_Data" / "resources.assets"
    if not assets.is_file():
        print(f"missing: {assets}", file=sys.stderr)
        return 2
    probe(assets, "." if args.all else args.pattern, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
