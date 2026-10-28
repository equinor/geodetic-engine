---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
language_info:
    name: python
---

# Matching a legacy dataset to a stored grid

The SDU note describes how to assign a legacy dataset, which arrives with only
its own corners, to a bin grid that is already stored, instead of storing yet
another definition. {mod}`geodetic_engine.bingrid.matching` implements it. It
is an optional module, not part of the `bingrid` API:

```{code-cell} python
from geodetic_engine.bingrid import Handedness, P6Parameters, corners_from_p6
from geodetic_engine.bingrid.matching import StoredBinGrid, match_bin_grid
from geodetic_engine.geodesy import transform

# The SDU note's Table 2 grid, stored in NAD27 / UTM zone 15N (metres).
table_2 = P6Parameters(
    origin_i=14100, origin_j=5161,
    origin_easting=423081.91, origin_northing=3227689.59,
    bin_width_i=30.0, bin_width_j=25.0, bearing_j=2.48019694,
    handedness=Handedness.RIGHT, increment_j=4,
)
store = [StoredBinGrid("table-2", table_2, "EPSG:26715")]

# A dataset loaded from it, delivered in NAD27 / BLM 15N (ftUS).
metres = corners_from_p6(
    table_2, inline_range=(14200, 15000), crossline_range=(5201, 7201)
)
dataset_corners = metres.with_coordinates(
    transform("EPSG:26715", "EPSG:32065", metres.coordinates).coordinates
)

result = match_bin_grid(dataset_corners, "EPSG:32065", store, increment_j=4)
result.best.grid.key   # result.best: the BinGridMatch to assign, or None to define a new grid
```

A stored grid matches if it puts every dataset corner within half of the
smaller real spacing between the dataset's loaded traces. When several match,
the order of preference is: a grid on the dataset's own map grid (the same
datum, projection and linear unit, whatever the axis order and whether or not
bound to WGS 84), then a grid stored at the dataset's increments, then the
smallest distance.

The note makes one exception to comparing in the dataset's own CRS: NAD27 data
in US survey feet against grids stored in metres. This module allows the
general form of it, a stored grid in any projected CRS on the dataset's datum,
compared after an exact conversion. It never compares across a datum change,
because that needs a transformation, and a match must not choose one.