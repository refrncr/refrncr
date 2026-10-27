document.getElementById("add-option").addEventListener("click", () => {
    const option = document.createElement("option");
    option.value = "late";
    option.textContent = "Late team";
    document.getElementById("team").append(option);
  });
  document.getElementById("set-value").addEventListener("click", () => {
    document.getElementById("country").value = "DE";
  });
  document.getElementById("focus-native").addEventListener("click", () => {
    document.getElementById("status").focus();
  });
  document.getElementById("status").addEventListener("change", () => {
    const counter = document.getElementById("changes");
    counter.textContent = String(Number(counter.textContent) + 1);
  });
