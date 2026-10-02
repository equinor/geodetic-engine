// Every top-level branch folds when its title is clicked, and the reader's choice
// is kept across pages for the rest of the browser session.
const OPEN_BY_DEFAULT = ["/getting-started/", "/user-guide/"];
const STORAGE_KEY = "geodetic-docs-sidebar";

const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? "{}");

for (const item of document.querySelectorAll(".sidebar-tree li.toctree-l1.has-children")) {
  const link = item.querySelector(":scope > a.reference");
  const box = item.querySelector(":scope > .toctree-checkbox");
  const children = item.querySelector(":scope > ul");
  if (!link || !box || !children) continue;
  const key = new URL(link.href).pathname;

  box.checked = saved[key] ?? (box.checked || OPEN_BY_DEFAULT.some((part) => key.includes(part)));
  box.addEventListener("change", () => {
    saved[key] = box.checked;
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
  });

  // The title now folds instead of navigating, so link the section's own page here.
  const overview = document.createElement("li");
  overview.className = item.classList.contains("current-page")
    ? "toctree-l2 current current-page"
    : "toctree-l2";
  const overviewLink = document.createElement("a");
  overviewLink.className = "reference internal";
  overviewLink.href = link.href;
  overviewLink.textContent = "Overview";
  overview.append(overviewLink);
  children.prepend(overview);

  link.addEventListener("click", (event) => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey) return;
    event.preventDefault();
    box.checked = !box.checked;
    box.dispatchEvent(new Event("change"));
  });
}
