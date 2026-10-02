// A glossary entry is highlighted by :target, which lasts until the URL fragment
// changes. Clicking anywhere that is not a link changes it, so the highlight clears.
document.addEventListener("click", (event) => {
  if (event.target.closest("a")) return;
  if (!document.querySelector("dl.glossary > dt:target")) return;
  // No element has this id, so nothing is targeted and nothing scrolls.
  location.replace(location.pathname + location.search + "#-");
  history.replaceState(null, "", location.pathname + location.search);
});
