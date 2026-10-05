"""Run first in every kernel the documentation build executes a page in.

``docs/conf.py`` names this file in ``PYTHONSTARTUP`` for the build's kernels
only; a notebook opened by hand never runs it. Everything happens inside
``_setup`` so that no name leaks into the pages' namespace.
"""


def _setup() -> None:
    import os
    from typing import Any

    import plotly.io as pio
    from plotly.io.base_renderers import MimetypeRenderer

    # open_in_browser() would otherwise open a tab on the machine building the docs.
    os.environ["BROWSER"] = "true"
    # plotly copies the renderer for every figure, so this state lives outside it.
    plotly_js_sent: list[bool] = []
    # plotly sizes a figure while the page is still being laid out; refit it after.
    refit = (
        "var gd = document.getElementById('{plot_id}');"
        " var refit = function () { Plotly.Plots.resize(gd); };"
        " if (document.readyState === 'complete') { refit(); }"
        " else { window.addEventListener('load', refit); }"
        " new ResizeObserver(refit).observe(gd.parentElement);"
    )

    class StaticPage(MimetypeRenderer):
        """Figures as HTML a static page can show, plotly.js with the first.

        plotly's own HTML renderers load MathJax 2 from a CDN, which breaks the
        MathJax 3 that Sphinx renders a page's equations with.
        """

        def to_mimebundle(self, fig_dict: dict[str, Any]) -> dict[str, str]:
            # A fixed width overflows the page's column; fill it instead.
            fig_dict.setdefault("layout", {}).pop("width", None)
            html = pio.to_html(
                fig_dict,
                config={"displaylogo": False, "responsive": True},
                include_plotlyjs=not plotly_js_sent,
                post_script=refit,
                full_html=False,
                default_height=560,
            )
            plotly_js_sent.append(True)
            return {"text/html": html}

    pio.renderers["static_page"] = StaticPage()
    pio.renderers.default = "static_page"


_setup()
del _setup
