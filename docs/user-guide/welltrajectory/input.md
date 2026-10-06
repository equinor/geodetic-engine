---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Building the input

```{code-cell} python
:tags: [remove-cell]

%xmode Minimal

import pandas as pd

pd.set_option("display.precision", 3)
pd.set_option("display.width", 160)
```

{class}`~geodetic_engine.welltrajectory.TrajectoryInput` holds everything a
trajectory is computed from: the survey, the wellhead, the CRS, what the
azimuths are measured from, and the optional settings.
{meth}`~geodetic_engine.welltrajectory.TrajectoryInput.compute` turns it into
a {class}`~geodetic_engine.welltrajectory.WellTrajectory`. Build one from the
form your survey is in; each form has its own constructor:

| Constructor | The survey as |
|---|---|
| {meth}`~geodetic_engine.welltrajectory.TrajectoryInput.from_arrays` | One array or list per quantity: numpy arrays, lists, tuples or pandas Series. |
| {meth}`~geodetic_engine.welltrajectory.TrajectoryInput.from_records` | One row per station: tuples, lists, or mappings with the keys `md`, `inclination` and `azimuth`. |
| {meth}`~geodetic_engine.welltrajectory.TrajectoryInput.from_dataframe` | A pandas DataFrame, with any column names. |
| {meth}`~geodetic_engine.welltrajectory.TrajectoryInput.from_csv` | A CSV survey file: settings in a header, then a table with any column names; see [the format](#the-survey-file-format). |
| {meth}`~geodetic_engine.welltrajectory.TrajectoryInput.from_osdu_payload` | An OSDU `convertTrajectory` request body. |

## What an input holds

Four settings are required. The others have defaults.

| Setting | Required | Default | Meaning |
|---|---|---|---|
| `survey` | yes | | The stations: measured depth, inclination and azimuth, with their units. A {class}`~geodetic_engine.welltrajectory.Survey`, which every constructor builds for you. |
| `wellhead` | yes | | Where MD and TVD count from: `(x, y)` or `(x, y, z)` in the CRS, or a {class}`~geodetic_engine.welltrajectory.Wellhead`. `z` is the elevation, in `z_unit`, 0 if left out. |
| `crs` | yes | | The trajectory CRS: an EPSG code, WKT, PROJJSON, an OSDU `persistableReference` or a {term}`bound CRS`. Resolved when the trajectory is computed. |
| `north_reference` | yes | | What the azimuths are measured from: `"GN"`, grid north, or `"TN"`, true north. |
| `md_unit` | | `"m"` | Unit of the measured depths. |
| `angle_unit` | | `"degree"` | Unit of the inclinations and azimuths. |
| `method` | | `"AzimuthalEquidistant"` | How the offsets are georeferenced in the CRS; see {doc}`georeferencing`. |
| `z_unit` | | `"m"` | Unit of the wellhead elevation and of every depth, elevation and offset reported. |
| `md_step` | | None | Also a point every `md_step` of MD, in `md_unit`, between the stations. |
| `md_points` | | none | Also a point at each of these measured depths, in `md_unit`. |
| `name` | | None | The well's name, carried to the trajectory and used as its label in plots. |

Every constructor takes `wellhead`, `crs` and `north_reference` as keyword
arguments, and the optional settings as further keyword arguments, listed in
{class}`~geodetic_engine.welltrajectory.TrajectoryOptions`. Units are given as
{doc}`surveys` describes: symbols, OSDU unit ids or OSDU unit
`persistableReference`s.

## From arrays

```{code-cell} python
import numpy as np

from geodetic_engine.welltrajectory import TrajectoryInput

well = TrajectoryInput.from_arrays(
    md=np.array([0.0, 500.0, 1500.0, 2500.0]),
    inclination=np.array([0.0, 20.0, 60.0, 70.0]),
    azimuth=np.array([10.0, 30.0, 45.0, 50.0]),
    wellhead=(500000.0, 6600000.0, 25.0),
    crs="EPSG:32631",
    north_reference="GN",
    name="Well A",
)
well.compute().to_dataframe()
```

Lists work as well as numpy arrays. Leave `azimuth` out for an
inclination-only survey, which is then taken to head north throughout.

## From rows

One row per station, as a tuple or list, `(md, inclination, azimuth)`:

```{code-cell} python
rows = [(0, 0, 10), (500, 20, 30), (1500, 60, 45), (2500, 70, 50)]
by_rows = TrajectoryInput.from_records(
    rows, wellhead=(500000.0, 6600000.0, 25.0), crs="EPSG:32631", north_reference="GN"
)
by_rows.survey.md, by_rows.survey.azimuth
```

or as a mapping, which is how JSON usually holds stations:

```{code-cell} python
stations = [
    {"md": 0, "inclination": 0, "azimuth": 10},
    {"md": 500, "inclination": 20, "azimuth": 30},
    {"md": 1500, "inclination": 60, "azimuth": 45},
]
TrajectoryInput.from_records(
    stations, wellhead=(500000.0, 6600000.0), crs="EPSG:32631", north_reference="GN"
).survey.inclination
```

Rows of two values, `(md, inclination)`, are an inclination-only survey. Every
row must have the same form.

## From a DataFrame

Name the columns to read. Other columns are ignored, so a table exported with
TVD, offsets or tool readings can be passed as it is:

```{code-cell} python
import pandas as pd

exported = pd.DataFrame(
    {
        "MD": [0.0, 500.0, 1500.0, 2500.0],
        "INC": [0.0, 20.0, 60.0, 70.0],
        "AZI": [10.0, 30.0, 45.0, 50.0],
        "TVD": [0.0, 489.9, 1241.9, 1664.3],
    }
)
from_table = TrajectoryInput.from_dataframe(
    exported,
    md_column="MD",
    inclination_column="INC",
    azimuth_column="AZI",
    wellhead=(500000.0, 6600000.0, 25.0),
    crs="EPSG:32631",
    north_reference="GN",
)
from_table.to_dataframe()
```

`azimuth_column=None` reads an inclination-only survey. A column that is not
there is named, with the columns that are:

```{code-cell} python
:tags: [raises-exception]

TrajectoryInput.from_dataframe(
    exported, wellhead=(500000.0, 6600000.0), crs="EPSG:32631", north_reference="GN"
)
```

## From a survey file

A survey file holds a whole input: the settings in a header, then the
stations in a table. The package ships a synthetic one in its
`example_data` folder:

```{code-cell} python
from importlib.resources import files

EXAMPLE = files("geodetic_engine.welltrajectory") / "example_data" / "synthetic_well.csv"
print(*EXAMPLE.read_text(encoding="utf-8").splitlines()[:17], "...", sep="\n")
```

```{code-cell} python
synthetic = TrajectoryInput.from_csv(EXAMPLE)
print(synthetic.name, "|", synthetic.crs, "|", synthetic.wellhead, "|", synthetic.north_reference)

trajectory = synthetic.compute()
print(f"{len(trajectory)} stations to TD at MD {trajectory.md[-1]:g} m, TVD {trajectory.tvd[-1]:.1f} m")
```

### The survey file format

The file is UTF-8 text. A byte order mark, as spreadsheet programs write, is
skipped, and text that is not UTF-8 is read as Latin-1, as older software
writes it.

**Header.** Lines starting with `#`, above the table. A line `# key: value`
states one setting, and the value runs to the end of the line, colons and
all. Keys are read in any case, with spaces, hyphens or underscores between
their words, or none: `Wellhead X`, `wellhead-x` and `WellheadX` are all
`wellhead_x`. A trailing `s` is dropped, so `Depth units` is `depth_unit`.
Each setting is given at most once, under whichever name.

| Key | Also | Required | Value |
|---|---|---|---|
| `crs` | `coordinate_system`, `coordinate_reference_system`, `trajectory_crs` | yes | The trajectory CRS, on one line: `EPSG:23031`, WKT, or an OSDU `persistableReference`. |
| `wellhead` | `origin`, `surface_location`, `reference_point` | yes, or `wellhead_x` and `wellhead_y` | The wellhead on one line: `x, y` or `x, y, z`, separated by commas, semicolons or spaces. |
| `wellhead_x`, `wellhead_y` | `x`, `easting`, `origin_x`, `surface_x`, `wellhead_easting`; `y`, `northing`, `origin_y`, `surface_y`, `wellhead_northing` | yes, or `wellhead` | The wellhead in the CRS, in its own units: easting and northing, or longitude and latitude. |
| `wellhead_z` | `z`, `elevation`, `kb`, `rkb`, `kb_elevation`, `rkb_elevation`, `datum_elevation`, `origin_z`, `surface_z`, `wellhead_elevation` | no, 0 | The elevation MD counts from, in `z_unit`. |
| `north_reference` | `north`, `north_ref`, `azimuth_reference` | yes | `GN` or `TN`; also `grid`, `grid north`, `true` or `true north`. |
| `md_unit` | `depth_unit` | no, `m` | Unit of the measured depths. |
| `angle_unit` | `angular_unit` | no, `degree` | Unit of the inclinations and azimuths. |
| `z_unit` | `elevation_unit`, `vertical_unit` | no, `m` | Unit of the wellhead elevation and of every depth and elevation reported. |
| `method` | | no, `AzimuthalEquidistant` | `AzimuthalEquidistant`, `GridNorthLocal`, `ENU` or `LMP`. |
| `md_step` | `md_interval` | no | Also a point every this much MD. |
| `md_points` | | no | Also a point at each of these measured depths, separated by commas. |
| `name` | `well`, `well_name`, `wellbore`, `wellbore_name` | no | The well's name. |

Any other line starting with `#` is a comment, `# Created by: Petrel`
included. A key within a letter or two of a known one, such as `md_unti`, is
refused as a likely misspelling rather than skipped; so is `Units`, which
could be either unit.

**Free text.** Other lines above the table are free text, as a survey
report's header is, and skipped, except a line `key: value` with one of the
keys above, such as `North Reference: Grid`. It states that setting the first
time the key appears, unless a `#` line states it. A key in free text is
never refused as misspelt.

**Table.** It starts at the first line naming the measured depth and
inclination columns, or else at the first line that is not a header line.
The columns are separated by commas, semicolons or tabs, whichever that line
uses, or else lined up with spaces, where two or more spaces end a name, so
`UTM E/W` is one; `delimiter=` names another.
The three the survey needs are found by their names, in any case and with
any punctuation, or named with an argument:

| Column | Found by the names | Or named with |
|---|---|---|
| Measured depth | `MD`, `Measured Depth`, `Depth`, `DEPT`, `MDepth` | `md_column` |
| Inclination | `Inclination`, `Inc`, `Incl`, `Inclin`, `DEVI`, `Deviation` | `inclination_column` |
| Azimuth | `Azimuth`, `Azi`, `Azim`, `HAZI`, `Direction`, or any name starting `Az`, such as `AZIM_GN` | `azimuth_column` |

A table without an azimuth column is an inclination-only survey. Other
columns, such as TVD, coordinates or remarks, are ignored. Two columns that
could both be one quantity, such as `AZIM_GN` and `AZIM_TN`, are refused:
name the one to read.

A unit in brackets after a column's name, as in `MD (ft)` or `Inc [deg]`, is
that column's unit. So is one in a line of units right below the names, a
line holding no number, as in `m RKB  deg  deg`; a word after the unit, such
as the datum `RKB`, is ignored. A unit given both ways must agree. An argument
giving another unit takes precedence; a header stating another is refused.
Annotated units must be recognized and measure the correct quantity, including
when an argument overrides them. An unsupported annotation never becomes a default.

Every further line is one station: plain numbers, with `.` as the decimal
point and no thousands separator. Blank lines are skipped; header and comment
lines may not appear inside the table.

### A file from other software

A file exported by another program is read as it is, when its names are among
those above:

```{code-cell} python
import io

exported = io.StringIO(
    "# Exported by: SurveyTool 4.2\n"
    "# Well name: A-10\n"
    "# Coordinate system: EPSG:32631\n"
    "# Origin: 500000; 6600000; 25\n"
    "# Azimuth reference: Grid north\n"
    "Measured Depth (ft);INCL;AZIM_GN;TVD (ft);Remark\n"
    "0;0;10;0;tie-in\n"
    "1500;20;30;1469.6;\n"
    "4500;60;45;3664.1;TD\n"
)
other = TrajectoryInput.from_csv(exported)
print(other.name, "|", other.wellhead, "|", other.north_reference, "|", other.survey.md_unit)
other.to_dataframe()
```

Columns under other names are named with `md_column`, `inclination_column`
and `azimuth_column`, with or without the unit in brackets:

```{code-cell} python
plan = io.StringIO("DEPTH_M,I,A,X,Y\n0,0,10,500000,6600000\n500,20,30,500043,6600074\n")
TrajectoryInput.from_csv(
    plan,
    md_column="DEPTH_M",
    inclination_column="I",
    azimuth_column="A",
    wellhead=(500000.0, 6600000.0),
    crs="EPSG:32631",
    north_reference="GN",
).to_dataframe()
```

### A survey report

A survey report, as a well database exports it, is read as it is too. The
package ships the report of Volve F-1, a real well, from the Volve field data
set. Below its free text, the
columns are lined up with spaces, with their units underneath:

```{code-cell} python
REPORT = files("geodetic_engine.welltrajectory") / "example_data" / "volve_f1_survey.txt"
print(*REPORT.read_text(encoding="latin-1").splitlines()[37:48], "...", sep="\n")
```

The free text gives the name and the north reference, and the line of units
the units. The report names its CRS only in words, ED50 and UTM zone 31N, and
its survey starts at the wellhead on the seabed, 91 m below sea level, so
those two are passed:

```{code-cell} python
volve = TrajectoryInput.from_csv(
    REPORT, wellhead=(435046.488, 6478566.687, -91.0), crs="EPSG:23031"
)
print(volve.name, "|", volve.north_reference, "|", volve.survey.md_unit, "|", len(volve.survey.md), "stations")
```

### Arguments fill in and take precedence

Keyword arguments fill in what the header leaves out, so a bare table works:

```{code-cell} python
bare = io.StringIO("md,inclination,azimuth\n0,0,10\n500,20,30\n1500,60,45\n")
TrajectoryInput.from_csv(
    bare, wellhead=(500000.0, 6600000.0), crs="EPSG:32631", north_reference="GN"
).to_dataframe()
```

and they take precedence over what the header states, so one file can be
computed with other settings:

```{code-cell} python
lmp = TrajectoryInput.from_csv(EXAMPLE, method="LMP", z_unit="ft")
lmp.method, lmp.z_unit, lmp.wellhead
```

`wellhead=` replaces the header's wellhead, however it is stated.

### Files out of format

A key that looks misspelt is refused, with the line at fault:

```{code-cell} python
:tags: [raises-exception]

TrajectoryInput.from_csv(
    io.StringIO(
        "# crs: EPSG:32631\n# wellhead: 500000, 6600000\n"
        "# north_refrence: GN\nmd,inclination,azimuth\n0,0,0\n500,20,30\n"
    )
)
```

and so is a table in which two columns could be the azimuths:

```{code-cell} python
:tags: [raises-exception]

TrajectoryInput.from_csv(
    io.StringIO("MD,INCL,AZIM_GN,AZIM_TN\n0,0,0,0\n500,20,30,29.2\n"),
    wellhead=(500000.0, 6600000.0),
    crs="EPSG:32631",
    north_reference="GN",
)
```

and so is one that leaves out a required setting, which the arguments do not
give either:

```{code-cell} python
:tags: [raises-exception]

TrajectoryInput.from_csv(io.StringIO("md,inclination\n0,0\n500,5\n"), crs="EPSG:32631")
```

## From an OSDU request body

{meth}`~geodetic_engine.welltrajectory.TrajectoryInput.from_osdu_payload` reads
the body of an OSDU `convertTrajectory` request, as a mapping or as JSON text.
Its fields map onto the input one for one:

| Body field | Input setting |
|---|---|
| `trajectoryCRS` | `crs` |
| `azimuthReference` | `north_reference` |
| `referencePoint`, `{x, y, z}` | `wellhead` |
| `inputStations`, `[{md, inclination, azimuth}, ...]` | `survey`, with angles in degrees |
| `inputKind`, `"MD_Incl"` | An inclination-only survey: any azimuths are ignored. |
| `unitMD`, else `unitZ` | `md_unit` |
| `unitZ` | `z_unit` |
| `method`, where `"GNL"` is `GridNorthLocal` | `method` |
| `MD_i.md_i` | `md_points` |
| `MD_i.md_interval`, and `interpolate`, a point every 100 | `md_step`, the finer of the two when both are given and one is a multiple of the other |
| `unitXY` | Checked to be the CRS's own unit, and not stored: the wellhead is never rescaled. |

```{code-cell} python
body = {
    "trajectoryCRS": "EPSG:32631",
    "azimuthReference": "GN",
    "unitZ": "ft",
    "unitMD": "m",
    "referencePoint": {"x": 500000.0, "y": 6600000.0, "z": 82.0},
    "inputStations": [
        {"md": 0, "inclination": 0, "azimuth": 10},
        {"md": 500, "inclination": 20, "azimuth": 30},
        {"md": 1500, "inclination": 60, "azimuth": 45},
    ],
    "method": "GNL",
    "MD_i": {"md_i": [1000.0]},
}
from_body = TrajectoryInput.from_osdu_payload(body)
print(from_body.method, from_body.survey.md_unit, from_body.z_unit, from_body.md_points)
from_body.compute().to_dataframe()[["md", "tvd", "z", "is_survey_station"]]
```

The service returned the points `MD_i` asked for apart from the stations, as
`stations_i`. Here they are among the stations, in MD order, with
`is_survey_station` False.

## Checked when it is made

Everything that can be checked without the CRS is checked when the input is
made, before anything is computed: the units, the settings, and that the
survey describes a wellbore. The CRS is resolved by
{meth}`~geodetic_engine.welltrajectory.TrajectoryInput.compute`.

```{code-cell} python
:tags: [raises-exception]

TrajectoryInput.from_arrays(
    [0, 500, 400], [0, 10, 20], [0, 0, 0],
    wellhead=(500000.0, 6600000.0), crs="EPSG:32631", north_reference="GN",
)
```

```{code-cell} python
:tags: [raises-exception]

TrajectoryInput.from_arrays(
    [0, 500, 1500], [0, 10, 20], [0, 0, 0],
    wellhead=(500000.0, 6600000.0), crs="EPSG:32631", north_reference="magnetic",
)
```

A missing setting raises
{class}`~geodetic_engine.welltrajectory.InvalidInputError`, a survey that
cannot describe a wellbore
{class}`~geodetic_engine.welltrajectory.InvalidSurveyError`, and a unit that
is not recognised {class}`~geodetic_engine.welltrajectory.UnitError`.

## Changing a setting

An input is frozen. {func}`dataclasses.replace` gives a copy with some
settings changed, and checks it again:

```{code-cell} python
import dataclasses

every_100 = dataclasses.replace(synthetic, md_step=100, method="ENU")
every_100.method, every_100.md_step, len(every_100.compute())
```

That is also how one survey is compared across methods, CRSs or north
references; {doc}`georeferencing` does so.

## Writing a survey file

{meth}`~geodetic_engine.welltrajectory.TrajectoryInput.to_csv` writes an input
in the survey file format: to a file when given a path, or else as text.
Every setting goes in the header, defaults included, so the file states
everything the trajectory is computed with, and
{meth}`~geodetic_engine.welltrajectory.TrajectoryInput.from_csv` reads it back
exactly:

```{code-cell} python
text = every_100.to_csv()
print(*text.splitlines()[:15], "...", sep="\n")

again = TrajectoryInput.from_csv(io.StringIO(text))
print("\nthe same input:", again.md_step == every_100.md_step and np.array_equal(again.survey.md, every_100.survey.md))
```

A CRS given as something other than one line of text, such as a
`pyproj.CRS` or a bound CRS, is written as WKT.
{meth}`~geodetic_engine.welltrajectory.TrajectoryInput.to_dataframe` gives the
stations as a table, in the units they are stated in.
