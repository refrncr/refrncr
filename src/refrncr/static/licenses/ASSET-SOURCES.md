# UI asset sources and licences

All files in this inventory are served locally. The application makes no runtime request to
their upstream projects.

## Fonts

The application typefaces are shared with the refrncr website. Each weight is served as a Latin and a
Latin Extended subset, selected by `unicode-range`.

- Instrument Sans Medium 500, SemiBold 600 and Bold 700: `instrument-sans-{latin,latin-ext}-{500,600,700}-normal.woff2`,
  from the Fontsource package `@fontsource/instrument-sans` 5.3.0; licensed under
  `OFL-Instrument-Sans.txt`.
- IBM Plex Sans Regular 400, SemiBold 600 and Bold 700:
  `ibm-plex-sans-{latin,latin-ext}-{400,600,700}-normal.woff2`, from `@fontsource/ibm-plex-sans`
  5.3.0; licensed under `OFL-IBM-Plex-Sans.txt`.
- IBM Plex Mono Regular 400: `ibm-plex-mono-{latin,latin-ext}-400-normal.woff2`, from
  `@fontsource/ibm-plex-mono` 5.3.0; licensed under `OFL-IBM-Plex-Mono.txt`.

## Icons

Every icon is an inline SVG macro in `templates/components/icons.html`, so it takes the colour of
the text around it. They are the following icons from the official Lucide release `0.468.0`
(the inline macros stay on that release; only the Dataset icon sprite moved to `1.46.0`),
unchanged apart from the surrounding `class`, `aria-hidden` and `focusable` attributes:

| Macro | Lucide icon |
| --- | --- |
| `settings_icon` | Settings |
| `configure_icon` | Settings 2 |
| `home_icon` | House |
| `datasets_icon` | Table |
| `chevron_up_icon` / `chevron_down_icon` | Chevron Up / Chevron Down |
| `back_icon` | Arrow Left |
| `user_icon` / `team_icon` | User / Users |
| `logout_icon` | Log Out |
| `file_pen_icon` | File Pen |
| `history_icon` | History |
| `search_icon` | Search |
| `audit_icon` | Scroll Text |
| `download_icon` / `upload_icon` | Download / Upload |
| `plus_icon` | Plus |
| `lock_icon` | Lock |
| `info_icon` | Info |
| `close_icon` | X |
| `key_icon` | Key Round |
| `navigation_icon` | Panel Left Close / Panel Left Open |

`key_icon` is tinted with the accent colour through the `.key-icon` rule. See
`LICENSE-Lucide.txt` for the upstream ISC/MIT notices.

## Dataset icon selection (REF-162)

Dataset icons use the official `lucide-static` **1.46.0** sprite, copied unchanged
from `https://registry.npmjs.org/lucide-static/-/lucide-static-1.46.0.tgz` to
`static/images/lucide-1.46.0.svg`. Its 1,838 named symbols support manual icon-name
entry; the full sprite is needed because users choose names at runtime. The
511,302-byte file is local and cacheable; there is no runtime CDN or package dependency.

Sprite SHA-256: `ff8f757982592b7103c82acbe0922fc530d296ede376fd7dfca38ceaf926ebcf`.
`LICENSE-Lucide.txt` is the package LICENSE of that release, copied verbatim; it now
also names the Feather-derived icons and their MIT terms explicitly.
See [Lucide static assets](https://lucide.dev/guide/static).

### Compatibility with previously saved icons (REF-165)

`lucide-legacy-0.468.0.svg` retains the 74 symbols available in the previously
shipped 0.468.0 sprite but absent from 1.46.0, including `history` and retired
brand icons. The symbol markup and geometry are copied unchanged from that
release; only the outer SVG wrapper and subset selection differ. The source is
`https://registry.npmjs.org/lucide-static/-/lucide-static-0.468.0.tgz`.
Its original notice is retained in `LICENSE-Lucide-0.468.0.txt`.

Compatibility sprite SHA-256:
`54279ae9585b7deb66c0813dcfb1d1735405a736d3f36b5d75a10b741389f554`.

The server aliases each supported icon name to a symbol in one of these two
fixed local sprites. Existing names remain unchanged in storage; reading or
saving metadata does not clear them. There is no schema migration, user-supplied
SVG, external request or approximate substitution of a retired brand icon.

The suggestions (REF-165, product-owner selection) are `landmark`, `banknote`,
`languages`, `earth`, `atom`, `flask-conical`, `test-tube-diagonal`, `tag`, `bookmark`,
`signpost-big`, `building`, `layers`, `vector-polygon`, `paint-bucket`, `bot`, `network`,
`database`, `scale`, `file-box`, `target`, `ruler-dimension-line`, `gallery-vertical-end`,
`clock`, `vector-square`, `settings`, `file-sliders`, `hand-coins`, `credit-card`,
`building-complex`, `users-round`, `user-round`, `wrench`, `refresh-ccw`, `zap` and
`cable`, with `table` as the default. Names outside those suggestions are
accepted when present in the pinned sprite. The server reads the local symbol IDs
once and stores only a matching canonical name. Input is bounded to 64 characters
before trimming/lowercasing; unknown, malformed, unavailable, oversized or non-text
input becomes null without a validation error. There is no truncation or raw-input storage.
The render helper independently checks membership before composing a reference
inside the one fixed sprite URL. No input becomes SVG, HTML or a file/template path.
Empty or unavailable names render Table. Icons remain decorative alongside names.
The interface icons in `components/icons.html` continue to use their existing macros.

## Product branding (REF-162)

The product owner supplied these normative SVG files with the implementation
request. They are copied byte-for-byte into `static/images/`; colour and geometry
are unchanged. They are product assets, not Lucide assets; no separate third-party
licence was supplied with them.

Source: `docs/refrncr-brand-kit`.

- `refrncr-wordmark-primary.svg`: navigation, mobile bar, setup and login wordmark.
- `refrncr-loader.svg`: animated mark above the login wordmark; hidden with
  `prefers-reduced-motion: reduce`.
- `favicon.svg`: favicon.

The wordmark is decorative (`aria-hidden`, empty alt text); the brand links carry
the accessible name "REFRNCR home". Explicit image dimensions reserve space.
All resources are local.

Earlier branding files (`07-static-logo-256px.svg`, `02-animated-grow-shrink.svg`,
`06-favicon-16px.svg`) are no longer referenced by the application. The old block-logo files
were removed after checking consumers.

## Swagger UI 5.33.0 (REF-109)

Locally served, byte-for-byte files from the pinned npm `swagger-ui-dist` 5.33.0
[distribution](https://registry.npmjs.org/swagger-ui-dist/-/swagger-ui-dist-5.33.0.tgz),
[upstream project](https://github.com/swagger-api/swagger-ui). No CDN or online
validator is contacted at runtime. Apache-2.0 LICENSE/NOTICE and bundled dependency
notices are included as `LICENSE-Swagger-UI.txt`, `NOTICE-Swagger-UI.txt` and
`LICENSE-Swagger-UI-bundle.txt`.

The downloaded archive matched npm's SHA-512 integrity value:
`sha512-wpdK+m6BU5yj6pmUdMskZVTSWYG4DLglAx3sIhylloY37i8O37IrH+YEpqdXNfpaTGxILRBFzUqLF2jKqbfI7A==`.

| Local file | SHA-256 |
| --- | --- |
| `static/js/swagger-ui-bundle.js` | `62df541529080464a7660adc793eab7128c6193ce3be24ddc1e0e0a4a63edc2f` |
| `static/css/swagger-ui.css` | `1ac324f7dcd27e4b9386b4bd6421271ec147e922a22c05ba24b11515e9aa6321` |

The narrow pre-commit exclusions preserve these upstream bytes and permit the
1.59 MB JavaScript distribution; first-party files retain all normal checks.
Source maps and unused OAuth/standalone assets are not shipped. Future upgrades
must refresh provenance/hashes/licenses and rerun schema, CSP, credential privacy,
keyboard, responsive and package checks. Vendoring requires explicit maintenance;
this is not an automated vulnerability-scanning or supply-chain-signature claim.
