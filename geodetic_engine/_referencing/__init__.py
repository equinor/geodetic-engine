"""The private layer below ``geodesy`` and ``persistablereference``.

What both need and neither owns: reading proj.db, OSDU record ids, and the
exceptions raised here. It imports neither package, which lets
``persistablereference`` read payloads without importing ``geodesy``.
``geodesy`` re-exports what is public. ``lint-imports`` enforces the layering.
"""
