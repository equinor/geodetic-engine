"""The private layer below ``geodesy`` and ``persistablereference``.

What both need and neither owns: reading proj.db, OSDU record ids, the
PROJJSON of an operation, the ``xy`` axis-order model, the Helmert algebra,
and the exceptions raised here. It imports neither package, which lets
``persistablereference`` read payloads without importing ``geodesy``.
``geodesy`` re-exports what is public. ``lint-imports`` enforces the layering.
"""
