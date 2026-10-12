// Apply the saved theme before first paint (no flash). Storage can be blocked, so it is wrapped in try/catch.
(function () {
  try {
    var t = localStorage.getItem("hisaab-theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) { /* private mode / blocked storage: follow the system theme */ }
})();
