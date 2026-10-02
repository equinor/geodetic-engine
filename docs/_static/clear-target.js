// A linked-to element (a heading's section, a glossary term, a footnote) is
// highlighted by :target, which lasts until the URL fragment changes. Clicking
// anywhere that is not a link or a control changes it, so the highlight clears.
document.addEventListener("click", (event) => {
  if (event.target.closest("a, button, input, label, select, textarea, summary")) return;
  if (!document.querySelector(":target")) return;
  // No element has this id, so nothing is targeted and nothing scrolls.
  location.replace(location.pathname + location.search + "#-");
  history.replaceState(null, "", location.pathname + location.search);
});
