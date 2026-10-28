"""Settings shared by the whole test suite.

The Hypothesis property tests run deterministic examples by default, so that a
run reproduces on every machine. Pass ``--hypothesis-profile=geodetic-engine-random``
to draw fresh examples instead, as CI does in a separate step: a strategy or
tolerance that only holds for the fixed examples is found there, not when a
Hypothesis or Python upgrade changes which examples the fixed seed gives.

The suite runs against PROJ's stock database, whatever the installation ships:
``GEODETIC_ENGINE_OSDU_DB=0`` is set before any test imports the package, and
every subprocess inherits it. tests/test_bundled_database.py clears it.
"""

import os

from hypothesis import settings

os.environ["GEODETIC_ENGINE_OSDU_DB"] = "0"

# Here rather than in tests/bingrid: pytest loads this file before it applies
# --hypothesis-profile, whichever test paths it is given.
#
# Deterministic, like the dataset sampling elsewhere in this suite: the same
# examples on every run and machine, and no example database written to disk.
settings.register_profile(
    "geodetic-engine", derandomize=True, database=None, deadline=None
)
settings.register_profile(
    "geodetic-engine-random",
    derandomize=False,
    database=None,
    deadline=None,
    max_examples=300,
)
settings.load_profile("geodetic-engine")
