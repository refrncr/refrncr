// Restoring an old rendered review must recheck its revision and current authority.
window.addEventListener("pageshow", (event) => {
  if (event.persisted && document.querySelector("[data-version-review]")) {
    window.location.reload();
  }
});
