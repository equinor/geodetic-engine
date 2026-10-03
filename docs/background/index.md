# Background

Why the package works the way it does. None of this is needed to use the API,
but it explains the refusals and the design choices behind them.

- {doc}`architecture`: the two workflows, build time and runtime, and what
  each package is responsible for.
- {doc}`guarantees`: what plain PROJ does and what this package does instead,
  and why.
- {doc}`axis-order`: declared axis order versus value order, and the PROJ gap
  that is worked around.
- {doc}`bound-crs`: early binding, how a bound CRS is stored, and collapsing a
  chain into one step.
- {doc}`provenance`: what a result records, and how a custom database is
  traced back to its inputs.
- {doc}`/workarounds`: every defect or gap this package works around, with
  the condition for removing each workaround.

```{toctree}
:hidden:

architecture
guarantees
axis-order
bound-crs
provenance
/workarounds
```
