(() => {
  // This small blocking head script restores layout before the shell can paint.
  const root = document.documentElement;
  const storageKey = "refrncr.navigation.desktop";
  let desktopExpanded = true;
  try { desktopExpanded = window.sessionStorage.getItem(storageKey) !== "collapsed"; } catch (_) {
    // Storage can be unavailable in restricted browser contexts; keep in-page controls usable.
  }
  root.classList.toggle("navigation-collapsed", !desktopExpanded);

  // Record criteria (search, status) reload the page. Restore the scroll position of the
  // same page before its first paint, so the new page does not jump.
  const restoreScroll = (event) => {
    let saved = null;
    try {
      saved = JSON.parse(window.sessionStorage.getItem("refrncr.criteria"));
      // Deferred scripts have run by DOMContentLoaded; the saved state is used up then.
      if (event.type === "DOMContentLoaded") window.sessionStorage.removeItem("refrncr.criteria");
    } catch (_) {}
    if (!saved || saved.path !== location.pathname) return;
    const main = document.getElementById("content");
    if (main) main.scrollTop = saved.y;
    if (!main || main.scrollTop !== saved.y) document.scrollingElement.scrollTop = saved.y;
  };
  window.addEventListener("pagereveal", restoreScroll, {once: true});
  document.addEventListener("DOMContentLoaded", restoreScroll, {once: true});

  document.addEventListener("DOMContentLoaded", () => {
    const shell = document.querySelector("[data-responsive-shell]");
    const navigation = shell?.querySelector("[data-navigation]");
    if (!navigation) return;
    const openButton = shell.querySelector("[data-nav-open]");
    const closeButton = navigation.querySelector("[data-nav-close]");
    const drawer = shell.querySelector("[data-navigation-drawer]");
    const desktop = window.matchMedia("(min-width: 1024px)");
    const home = navigation.parentNode;
    const next = navigation.nextSibling;
    let opener = null;

    const controls = () => [...navigation.querySelectorAll(
      'a[href], button:not([disabled]), [tabindex="0"]'
    )].filter((element) => element.getClientRects().length > 0);
    const update = () => {
      const expanded = desktop.matches ? desktopExpanded : drawer.open;
      for (const button of [openButton, closeButton]) {
        button.setAttribute("aria-expanded", String(expanded));
        const label = button === openButton
          ? (desktop.matches ? "Expand navigation" : "Open navigation")
          : (desktop.matches ? "Collapse navigation" : "Close navigation");
        button.setAttribute("aria-label", label);
        button.title = label;
      }
      root.classList.toggle("navigation-collapsed", !desktopExpanded);
      root.classList.toggle("navigation-drawer-open", drawer.open);
    };
    const closeDrawer = (restoreFocus = true) => {
      if (!drawer.open) return;
      drawer.close();
      update();
      if (restoreFocus) opener?.focus();
      opener = null;
    };
    const setDesktop = (expanded) => {
      desktopExpanded = expanded;
      try { window.sessionStorage.setItem(storageKey, expanded ? "expanded" : "collapsed"); } catch (_) {}
      update();
      (expanded ? closeButton : openButton).focus();
    };
    openButton.addEventListener("click", () => {
      if (desktop.matches) { setDesktop(true); return; }
      opener = openButton;
      drawer.showModal();
      update();
      closeButton.focus();
    });
    closeButton.addEventListener("click", () => {
      if (desktop.matches) setDesktop(false);
      else closeDrawer();
    });
    drawer.addEventListener("cancel", (event) => {
      event.preventDefault();
      closeDrawer();
    });
    drawer.addEventListener("close", update);
    // A click completed on the backdrop closes; dragging from inside does not.
    let backdropPointer = false;
    drawer.addEventListener("pointerdown", (event) => { backdropPointer = event.target === drawer; });
    drawer.addEventListener("click", (event) => {
      if (backdropPointer && event.target === drawer) closeDrawer();
      backdropPointer = false;
    });
    navigation.addEventListener("click", (event) => {
      if (event.target.closest("a[href]")) closeDrawer(false);
    });
    drawer.addEventListener("keydown", (event) => {
      if (event.key !== "Tab") return;
      const items = controls();
      const first = items[0], last = items.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first?.focus();
      }
    });
    const resize = () => {
      const focused = document.activeElement;
      const focusInNavigation = navigation.contains(focused);
      closeDrawer(false);
      if (desktop.matches) home.insertBefore(navigation, next);
      else drawer.append(navigation);
      update();
      // Never strand keyboard focus in a hidden sidebar or a closed dialog.
      if (focusInNavigation || focused === openButton) {
        (desktop.matches && desktopExpanded ? closeButton : openButton).focus();
      }
    };
    desktop.addEventListener("change", resize);
    window.addEventListener("pageshow", (event) => {
      if (!event.persisted) return;
      // Back/forward cache restores an old document without rerunning the head script.
      try { desktopExpanded = window.sessionStorage.getItem(storageKey) !== "collapsed"; } catch (_) {}
      resize();
    });
    resize();
  }, {once: true});
})();
