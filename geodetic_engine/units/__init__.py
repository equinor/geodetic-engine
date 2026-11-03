"""Length and angle units, read the same way wherever this package takes one.

A unit is given in one of three forms:

* a symbol or name, in any case: ``"m"``, ``"metre"``, ``"ft"``, ``"ftUS"``,
  ``"km"``, ``"degree"``, ``"dega"``, ``"rad"``, ``"gon"``;
* an OSDU ``UnitOfMeasure`` record id, URL-encoded or not, such as
  ``"osdu:reference-data--UnitOfMeasure:ft:"``;
* an OSDU unit persistableReference: ``USO``, a scale and an offset, or
  ``UAD``, a rational polynomial that reduces to a scale.

A payload must state what it measures. Its ``baseMeasurement`` ancestry is
``Length`` or ``L`` for a length, and ``Plane_Angle``, ``Angle`` or ``A`` for
an angle, in any case: the OSDU CRS conversion service writes the names, the
OSDU unit catalogue the Energistics dimensions. Any other ancestry is refused,
a rate per length included, and so is a unit converting with an offset.
Nothing is defaulted: a length read in the wrong unit moves every position by
a plausible looking amount.

Public entry points:

* :func:`length_unit` and :func:`angle_unit` -- read a unit.
* :class:`Unit` -- what is read: its symbol, quantity and scale, and the
  payload it came from, to be written back unchanged.
* :class:`UnitError` -- a unit that cannot be read, or is not of the quantity
  needed. It is also a :class:`ValueError`.

Example:
    >>> from geodetic_engine.units import angle_unit, length_unit
    >>> feet = length_unit("osdu:reference-data--UnitOfMeasure:ft:")
    >>> feet.symbol, feet.quantity, feet.scale
    ('ft', 'length', 0.3048)
    >>> angle_unit("dega").scale
    0.017453292519943295
    >>> length_unit("dega")  # doctest: +ELLIPSIS
    Traceback (most recent call last):
    ...
    geodetic_engine.units.errors.UnitError: 'dega' is not a length unit...
"""

from geodetic_engine.units.errors import UnitError
from geodetic_engine.units.unit import Unit, angle_unit, length_unit

__all__ = ["Unit", "UnitError", "angle_unit", "length_unit"]
