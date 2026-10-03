// Furo's button cycles light, dark and auto, so with a dark system light takes two
// clicks (auto looks like dark). Toggle between light and dark only. No stored
// choice means auto, so the system decides until the first click.
document.addEventListener(
  "click",
  (event) => {
    if (!event.target.closest(".theme-toggle")) return;
    event.stopImmediatePropagation();
    const current = document.body.dataset.theme;
    const dark =
      current === "dark" ||
      (current !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    const next = dark ? "light" : "dark";
    document.body.dataset.theme = next;
    localStorage.setItem("theme", next);
  },
  true,
);
