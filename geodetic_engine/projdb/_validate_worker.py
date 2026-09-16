"""Private subprocess entry point for isolated database validation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pyproj import datadir

from geodetic_engine.projdb.validate import _validate_in_process


def main() -> int:
    """Read one validation request and return its JSON result."""
    try:
        request = json.load(sys.stdin)
        datadir.set_data_dir(request["data_dir"])
        result = _validate_in_process(
            Path(request["database"]),
            authorities=request["authorities"],
            imported=request["imported"],
        )
    except Exception as error:
        print(json.dumps({"error": f"{type(error).__name__}: {error}"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
