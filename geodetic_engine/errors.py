"""Base exception for everything this package raises."""


class GeodeticEngineError(Exception):
    """Root of the geodetic-engine exception hierarchy.

    Callers that want to catch any failure from this library, rather than a
    specific one, catch this.
    """
