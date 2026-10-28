# Scale factor and node increments

P6 bin widths are *ground* distances. EPSG's formulas multiply them by the bin
grid scale factor k to get map grid distances. So a grid squared from map grid
corners has bin width = increment × map grid distance / (node span × k). This
makes deriving, applying and inverting the parameters round-trip exactly for
any k, and k moves no position: only k × bin width enters the formulas.

`convert_bin_grid` derives k as EPSG defines it: the point scale factor of the
CRS the grid is squared in, taken at the centre of the grid, from PROJ. The bin
widths are then the ground spacing of the bins at the centre, the same in any
CRS. Where the projection's scale varies across the grid, the spacing elsewhere
in the grid differs from it: averaged over the grid, by about 1e-5 (1 cm per
km) for a 100 km grid and 4e-7 for a 20 km one. A projection that is not
conformal, such as Cassini-Soldner or an equal-area one, has a scale that
depends on direction, and no single k makes both bin widths ground distances.
Where the scale at the grid centre differs by more than 1e-6 between
directions, `convert_bin_grid` raises `UnsupportedCRSError` rather than derive
k. Pass `scale_factor` to state k yourself; `square_up` and `derive_p6` take k
as given, 1.0 by default.

Node increments are how the inline and crossline numbers step between adjacent
nodes: a grid numbered 1, 5, 9, ... in crossline has `increment_j=4`, and its
crossline bin width is the distance between those nodes. Corners that are not
whole increments apart cannot all be nodes, and are refused with
`InvalidCornersError`. Mis-location is reported in inline and crossline
numbers (`di`, `dj`), in bins (`di_bins`, `dj_bins`), and as a map grid
distance.