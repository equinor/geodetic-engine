# Seismic bin grids

A 3D seismic survey numbers its traces by inline and crossline (I, J). A bin
grid maps those numbers to easting and northing in a projected CRS.
`geodetic_engine.bingrid` does that arithmetic, and the checks around it,
following:

- EPSG Guidance Note 7-2, methods 9666 (right-handed, "P6 I=J+90") and 1049
  (left-handed, "P6 I=J-90"), as written in IOGP P6/11 files;
- the OSDU four-corner definition, in which corners A, B, C and D sit at
  (min I, min J), (min I, max J), (max I, min J) and (max I, max J);
- the SDU note *Geometric aspects of bin grids*, for deriving P6 parameters
  from four corners and measuring how far the corners are from a rectangle.

The package works with plain Python values and has no OSDU JSON in it. Values
are in `xy` order (easting, then northing; longitude, then latitude), as in the
rest of this library. Nothing in it is rounded, and every result has a
`to_json_dict()`.

## Pages in this section

```{toctree}
:maxdepth: 1

bingrid/p6
bingrid/conversion
bingrid/scale
bingrid/matching
bingrid/osdu-java
```

## Sources

The package is an independent implementation. PROJ runs the P6 conversion as
its affine operation, with the coefficients it documents for EPSG methods 9666
and 1049, and supplies the bin grid scale factor; the squaring follows the SDU
note. Third-party material is used in the test suite only, as references:

- the OSDU crs-conversion-service's test cases and expected outcomes, and a
  port of its Java squaring to compare with (Apache License 2.0);
- the SDU note *Geometric aspects of bin grids* and its workbook, by Bert
  Kampes (Shell), as published in that service's repository;
- the worked examples of EPSG Guidance Note 7-2 (IOGP).

Each data file in `tests/bingrid/data` names its source, and the repository's
`NOTICE` file lists them.
