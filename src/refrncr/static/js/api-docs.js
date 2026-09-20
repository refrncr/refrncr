/* Local Swagger integration. Credentials stay in the current page's memory. */
(() => {
  'use strict';
  const ui = SwaggerUIBundle({
    url: '/api/openapi.json',
    dom_id: '#swagger-ui',
    deepLinking: false,
    queryConfigEnabled: false,
    persistAuthorization: false,
    validatorUrl: null,
    supportedSubmitMethods: ['get'],
    withCredentials: false,
    syntaxHighlight: {activated: false},
    defaultModelsExpandDepth: -1,
    displayRequestDuration: false,
    plugins: [system => ({
      // Swagger's default curl/snippet output embeds the Authorization header.
      // Remove these components entirely, rather than hiding secret text with CSS.
      components: {
        curl: () => null,
        RequestSnippets: () => null,
        InfoContainer: ({getComponent, specSelectors}) => {
          const Markdown = getComponent('Markdown', true);
          return system.React.createElement('section', {className: 'info'},
            system.React.createElement(Markdown, {source: specSelectors.info().get('description')}));
        },
      },
    })],
    requestInterceptor(request) {
      const url = new URL(request.url, location.origin);
      if (url.origin !== location.origin ||
          !(url.pathname === '/api/openapi.json' || url.pathname === '/api/docs' ||
            url.pathname.startsWith('/api/v1/datasets')) || (request.method || 'GET').toUpperCase() !== 'GET') {
        throw new Error('Only local read-only API requests are supported.');
      }
      request.credentials = 'omit';
      return request;
    },
  });

  let opener = null;
  let modal = null;
  const root = document.getElementById('swagger-ui');
  root.addEventListener('click', event => {
    if (!modal) opener = event.target.closest('button');
  }, true);
  const focusable = () => Array.from(modal.querySelectorAll(
    'button:not([disabled]), input:not([disabled]), a[href], [tabindex="0"]'
  )).filter(element => element.getClientRects().length);
  new MutationObserver(() => {
    for (const block of root.querySelectorAll('pre:not([tabindex])')) {
      block.tabIndex = 0;
      block.setAttribute('role', 'region');
      block.setAttribute('aria-label', 'JSON example or response');
    }
    const next = root.querySelector('.dialog-ux');
    if (next) {
      const input = next.querySelector('input');
      if (input) {
        input.type = 'password';
        input.autocomplete = 'off';
        input.setAttribute('aria-label', 'API key');
      }
      for (const button of next.querySelectorAll('.auth-btn-wrapper button[aria-label]')) {
        button.setAttribute('aria-label', button.textContent.trim() + ' API key');
      }
    }
    if (next && next !== modal) {
      opener = opener || document.activeElement;
      modal = next;
      modal.setAttribute('role', 'dialog');
      modal.setAttribute('aria-modal', 'true');
      modal.setAttribute('aria-label', 'API key authorization');
      const close = modal.querySelector('.close-modal');
      if (close) close.setAttribute('aria-label', 'Close authorization');
      const input = modal.querySelector('input');
      if (input) {
        input.focus();
      }
    } else if (!next && modal) {
      modal = null;
      const target = opener && opener.isConnected && opener !== document.body
        ? opener : root.querySelector('.scheme-container .authorize');
      if (target) target.focus();
      opener = null;
    }
  }).observe(root, {childList: true, subtree: true});
  document.addEventListener('keydown', event => {
    if (!modal) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      ui.authActions.showDefinitions(false);
    } else if (event.key === 'Tab') {
      const items = focusable();
      const index = items.indexOf(document.activeElement);
      if (items.length && (index === -1 || (event.shiftKey ? index === 0 : index === items.length - 1))) {
        event.preventDefault();
        items[event.shiftKey ? items.length - 1 : 0].focus();
      }
    }
  });
  window.addEventListener('pagehide', () => {
    ui.authActions.logout(['HTTPBearer']);
    root.replaceChildren();
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) location.reload();
  });
})();
