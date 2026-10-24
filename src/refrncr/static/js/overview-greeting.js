// Overview greeting: the server renders a neutral greeting with the UTC calendar day;
// the browser replaces both with the viewer's local time of day and local date.
(() => {
  const header = document.querySelector("[data-overview-greeting]");
  if (!header) return;
  try {
    const now = new Date();
    const name = header.dataset.name;
    const salutation = header.querySelector("[data-overview-salutation]");
    if (name && salutation) {
      const hour = now.getHours();
      const part = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
      salutation.textContent = `${part}, ${name}`;
    }
    const date = header.querySelector("[data-overview-date]");
    if (date) {
      const parts = {};
      new Intl.DateTimeFormat("en-GB", {weekday: "long", day: "numeric", month: "long", year: "numeric"})
        .formatToParts(now).forEach((item) => { parts[item.type] = item.value; });
      date.textContent = `${parts.weekday} ${parts.day} ${parts.month} ${parts.year}`;
      const pad = (value) => String(value).padStart(2, "0");
      date.setAttribute("datetime", `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`);
    }
  } catch { /* Keep the server-rendered greeting. */ }
})();
