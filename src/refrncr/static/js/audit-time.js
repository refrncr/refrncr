(() => {
  if (window.refrncrLocalTime) return;
  const isoUTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$/;
  const formatTime = (value) => {
    if (!isoUTC.test(value)) return null;
    const date = new Date(value);
    if (!Number.isFinite(date.getTime()) || date.toISOString() !== value.slice(0, 23) + 'Z') return null;
    const parts = new Intl.DateTimeFormat(undefined, {
      year: "numeric", month: "short", day: "numeric", hour: "2-digit",
      minute: "2-digit", second: "2-digit", fractionalSecondDigits: 3,
      timeZoneName: "shortOffset", hourCycle: "h23",
    }).formatToParts(date);
    return parts.map((part) => part.type === "fractionalSecond" ? value.slice(20, 26) : part.value).join("");
  };
  // One compact notation across lists: "11 Sep 2026, 15:18:35" or, inside a
  // day group, "15:18:35"; audit rows use "15:18". Exact instants stay in the title.
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const compactTime = (value, mode) => {
    const date = new Date(value);
    const parts = {};
    new Intl.DateTimeFormat("en-GB", {
      year: "numeric", month: "numeric", day: "numeric", weekday: "long",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
    }).formatToParts(date).forEach((part) => { parts[part.type] = part.value; });
    const time = `${parts.hour}:${parts.minute}` + (mode === "minute" ? "" : `:${parts.second}`);
    const day = `${Number(parts.day)} ${months[Number(parts.month) - 1]} ${parts.year}`;
    return {key: `${parts.year}-${parts.month}-${parts.day}`, day, weekday: parts.weekday,
      text: mode !== "datetime" ? time : `${day}, ${time}`};
  };
  window.refrncrLocalTime = (root) => {
    root.querySelectorAll("time[data-local-time]").forEach((element) => {
      try {
        const value = element.getAttribute("datetime");
        const formatted = formatTime(value);
        if (!formatted) return;
        const mode = element.dataset?.localTime;
        if (mode === "time" || mode === "datetime" || mode === "minute") {
          const local = compactTime(value, mode);
          element.textContent = local.text;
          element.title = formatted;
          Object.assign(element.dataset, {localDay: local.day, localDayKey: local.key, localWeekday: local.weekday});
          return;
        }
        element.textContent = formatted;
      } catch { /* Keep the authoritative UTC fallback. */ }
    });
  };
  window.refrncrLocalTime(document);

  // Group rows per local day; each group starts with a day heading row.
  document.querySelectorAll("table[data-local-days]").forEach((table) => {
    const source = table.tBodies?.[0];
    if (!source?.rows) return;
    const rows = [...source.rows];
    const times = rows.map((row) => row.querySelector('time[data-local-time="time"], time[data-local-time="minute"]'));
    if (!rows.length || times.some((time) => !time?.dataset.localDayKey)) {
      // Never leave a bare time without its date.
      times.forEach((time) => { if (time?.dataset.localDayKey) time.textContent = `${time.dataset.localDay}, ${time.textContent}`; });
      return;
    }
    const columns = table.tHead?.rows[0]?.cells.length || 1;
    let group = null;
    let key = null;
    rows.forEach((row, index) => {
      const time = times[index];
      if (time.dataset.localDayKey !== key) {
        key = time.dataset.localDayKey;
        group = document.createElement("tbody");
        const heading = group.insertRow();
        heading.className = "table-day-row";
        const cell = document.createElement("th");
        cell.scope = "rowgroup";
        cell.colSpan = columns;
        cell.textContent = `${time.dataset.localWeekday} ${time.dataset.localDay}`;
        heading.append(cell);
        table.append(group);
      }
      group.append(row);
    });
    source.remove();
  });
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (zone) document.querySelectorAll("[data-local-timezone]").forEach((element) => { element.textContent = `(${zone})`; });
  } catch { /* Keep the UTC label. */ }

  const pad = (value) => String(value).padStart(2, "0");
  const localValue = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  document.querySelectorAll("form[data-audit-filters]").forEach((form) => {
    let timezone;
    try {
      timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (!timezone) return;
    } catch { return; /* Preserve working explicit-offset controls without Intl. */ }
    const dates = [...form.querySelectorAll("[data-audit-date]")];
    const originals = new Map();
    dates.forEach((input) => {
      const utc = input.value;
      const date = utc ? new Date(utc) : null;
      input.type = "datetime-local";
      input.step = "60";
      // Display whole minutes, but retain the precise instant of an untouched link,
      // including the second occurrence of a repeated daylight-saving hour.
      input.value = date ? localValue(date) : "";
      const original = {local: input.value, utc, edited: false};
      originals.set(input, original);
      input.addEventListener("input", () => { original.edited = true; });
    });
    document.getElementById("audit-time-help").textContent = `Dates use your browser timezone (${timezone}). Start is inclusive; end is exclusive. A newly entered repeated daylight-saving hour uses its first occurrence.`;
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const params = new URLSearchParams(new FormData(form));
      let invalid = false;
      dates.forEach((input) => {
        input.removeAttribute("aria-invalid");
        if (input.validity?.badInput) {
          invalid = true;
          input.setAttribute("aria-invalid", "true");
          return;
        }
        if (!input.value) { params.delete(input.name); return; }
        const original = originals.get(input);
        if (!original.edited && input.value === original.local) { params.set(input.name, original.utc); return; }
        const date = new Date(input.value);
        if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(input.value) ||
            !Number.isFinite(date.getTime()) || localValue(date) !== input.value) {
          invalid = true;
          input.setAttribute("aria-invalid", "true");
          return;
        }
        params.set(input.name, date.toISOString());
      });
      if (invalid) {
        const error = document.getElementById("audit-date-error");
        error.textContent = "Enter a valid local date and time. This time may not exist during a daylight-saving change.";
        error.hidden = false;
        error.focus();
        return;
      }
      for (const [key, value] of [...params]) if (!value) params.delete(key);
      // The action filter is a named form control and shadows form.action.
      const target = new URL(form.getAttribute("action"), window.location.href);
      target.search = params.toString();
      window.location.assign(target.href);
    });
  });
})();
