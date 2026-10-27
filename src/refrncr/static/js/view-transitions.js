(() => {
  // Run in the head, before the first paint: an incoming transition can be skipped
  // before pagereveal, which then exposes no transition object at all.
  const observed = new WeakSet();
  let skipOutgoing = false;
  const observe = (transition, outgoing = false) => {
    if (!transition || observed.has(transition)) return;
    observed.add(transition);
    const skip = window.matchMedia('(prefers-reduced-motion: reduce)').matches ||
      (outgoing && skipOutgoing);
    transition.ready.catch(error => {
      // Only expected cancellation of this specific transition is benign. In
      // particular, duplicate names and other InvalidStateErrors remain errors.
      if (error.name === 'AbortError' && (skip || outgoing || document.hidden)) return;
      if (error.name === 'InvalidStateError' &&
          error.message.endsWith('ViewTransition opt-in disabled')) return;
      throw error;
    });
    if (skip) transition.skipTransition();
  };
  observe(document.activeViewTransition);
  window.addEventListener('pagereveal', event => observe(event.viewTransition));
  window.addEventListener('pageswap', event => observe(event.viewTransition, true));
  // A non-HTML destination cannot run an incoming handler. Opt out at the source
  // for these links/forms, and for pages that must not take transition snapshots.
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (link) skipOutgoing = link.hasAttribute('data-no-view-transition');
  });
  document.addEventListener('submit', event => {
    skipOutgoing = event.target.hasAttribute('data-no-view-transition');
  });
  window.addEventListener('pageshow', () => { skipOutgoing = false; });
})();
