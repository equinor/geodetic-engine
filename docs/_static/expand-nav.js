// Every top-level branch folds when its title is clicked with the mouse, or
// when its chevron is activated by mouse or keyboard. The choice is kept across
// pages for the rest of the browser session. Keyboard and assistive-technology
// activation of the title still follows the link.
const OPEN_BY_DEFAULT = ["/getting-started/", "/user-guide/"];
const STORAGE_KEY = "geodetic-docs-sidebar";

const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? "{}");

let branchCount = 0;
for (const item of document.querySelectorAll(".sidebar-tree li.toctree-l1.has-children")) {
  const link = item.querySelector(":scope > a.reference");
  const box = item.querySelector(":scope > .toctree-checkbox");
  const toggle = item.querySelector(":scope > label");
  const children = item.querySelector(":scope > ul");
  if (!link || !box || !toggle || !children) continue;
  const key = new URL(link.href).pathname;

  // The chevron is Furo's label for a hidden checkbox, which a keyboard cannot
  // reach. Expose it as a button that reports whether the branch is open.
  children.id = `nav-branch-${branchCount++}`;
  toggle.setAttribute("role", "button");
  toggle.setAttribute("aria-label", `Toggle ${link.textContent.trim()}`);
  toggle.setAttribute("aria-controls", children.id);
  toggle.tabIndex = 0;
  toggle.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    box.checked = !box.checked;
    box.dispatchEvent(new Event("change"));
  });

  const syncState = () => toggle.setAttribute("aria-expanded", String(box.checked));
  box.checked = saved[key] ?? (box.checked || OPEN_BY_DEFAULT.some((part) => key.includes(part)));
  box.addEventListener("change", () => {
    saved[key] = box.checked;
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
    syncState();
  });
  syncState();

  // The title folds on a mouse click, so link the section's own page here.
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
    // A keyboard or screen-reader activation has no pointer position (detail 0).
    if (event.detail === 0) return;
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey) return;
    event.preventDefault();
    box.checked = !box.checked;
    box.dispatchEvent(new Event("change"));
  });
}
