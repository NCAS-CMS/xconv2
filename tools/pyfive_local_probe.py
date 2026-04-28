from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pyfive


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Probe repeated pyfive object access and chunk-index logging on a local file.",
    )
    parser.add_argument("file", type=Path, help="Path to local NetCDF/HDF5 file")
    parser.add_argument(
        "--var",
        default="/m01s03i245",
        help="Dataset path to access repeatedly (default: /m01s03i245)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=4,
        help="Number of repeated accesses (default: 4)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Keep pyfive logs visible while muting unrelated libraries.
    logging.getLogger("pyfive").setLevel(logging.INFO)

    with pyfive.File(str(args.file), "r") as handle:
        for i in range(args.repeats):
            ds = handle[args.var]
            # Touch shape only to avoid expensive data reads while still forcing object setup.
            print(f"access {i + 1}: shape={ds.shape}")


if __name__ == "__main__":
    main()
