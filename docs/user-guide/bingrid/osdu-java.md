(bingrid-java-service)=

# Where this differs from the OSDU Java service

The OSDU crs-conversion-service's acceptance test grid, without and with a
`toCRS`, is this package's regression case (`tests/bingrid/test_conversion.py`).
The with-toCRS case reproduces the Java service's Apache SIS results: the P6
origin to within 1e-5 ftUS, and the corners to the 3 decimals the Java service
writes.

The Java implementation's defects in the computation are not reproduced here;
those in its JSON handling are outside this package. Each defect has a test
that states the correct behaviour and is marked `java_defect` (`uv run pytest
-m java_defect`). `tests/bingrid/data/java_defects.json` records where each
defect is in the Java source.

| | Java service | Here |
| --- | --- | --- |
| D2 | Bin widths ignore k, but the formulas apply it | Exact round trips for any k |
| D3, D4 | Corner layout and geometry unchecked: division by zero, NaN | `InvalidCornersError`, `DegenerateBinGridError` |
| D5 | Grids in geographic CRSs accepted | `UnsupportedCRSError` |
| D13 | No outline; the documented ring is clockwise | Counterclockwise outline, in the map CRS and in WGS 84 |
| D14 | Request mutated; input corners lost | Input, converted and squared corners kept |
| D15 | Converts even when toCRS is the source CRS | No conversion |
| D16 | Corners rounded, P6 origin not | Nothing rounded |
| D18 | The request's k is written back, even into another CRS | k derived for the output CRS |