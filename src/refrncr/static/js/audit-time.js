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
  window.refrncrLocalTime = (root) => {
    root.querySelectorAll("time[data-local-time]").forEach((element) => {
      try {
        const formatted = formatTime(element.getAttribute("datetime"));
        if (formatted) element.textContent = formatted;
      } catch { /* Keep the authoritative UTC fallback. */ }
    });
  };
  window.refrncrLocalTime(document);

  const pad = (value) => String(value).padStart(2, "0");
  const localValue = (date, fraction) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${fraction}`;
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
      input.step = "0.001";
      // Native datetime-local controls accept milliseconds, not microseconds.
      // Keep the original full-precision instant until this displayed value changes.
      input.value = date ? localValue(date, (utc.match(/\.(\d+)/)?.[1] || "").padEnd(3, "0").slice(0, 3)) : "";
      // Preserve the exact instant of an unchanged filter during DST's repeated hour.
      originals.set(input, {local: input.value, utc});
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
        if (input.value === original.local) { params.set(input.name, original.utc); return; }
        const match = input.value.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?$/);
        const date = new Date(input.value);
        const fraction = (match?.[3] || "").padEnd(6, "0");
        if (!match || !Number.isFinite(date.getTime()) || localValue(date, fraction) !== `${match[1]}:${match[2] || "00"}.${fraction}`) {
          invalid = true;
          input.setAttribute("aria-invalid", "true");
          return;
        }
        params.set(input.name, date.toISOString().replace(/\.\d{3}Z$/, `.${fraction}Z`));
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
