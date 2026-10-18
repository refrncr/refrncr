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

Interface icons are inline SVG macros in `templates/components/icons.html`, so they take the
colour of the text around them. The following icons use the official Lucide release `0.468.0`
(the inline macros stay on that release; only the Dataset icon sprite moved to `1.46.0`),
unchanged apart from the surrounding `class`, `aria-hidden` and `focusable` attributes:

| Macro | Lucide icon |
| --- | --- |
| `settings_icon` | Settings |
| `configure_icon` | Settings 2 |
| `home_icon` | House |
| `datasets_icon` | Table |
| `network_icon` | [Network](https://raw.githubusercontent.com/lucide-icons/lucide/0.468.0/icons/network.svg) — Hierarchy navigation |
| `chevron_up_icon` / `chevron_down_icon` | Chevron Up / Chevron Down |
| `back_icon` | Arrow Left |
| `appearance_icon` | Sun Moon |
| `user_icon` / `team_icon` | User / Users |
| `logout_icon` | Log Out |
| `file_pen_icon` | File Pen |
| `square_pen_icon` | [Square Pen](https://raw.githubusercontent.com/lucide-icons/lucide/0.468.0/icons/square-pen.svg) — Hierarchy: Edit node |
| `table_properties_icon` | [Table Properties](https://raw.githubusercontent.com/lucide-icons/lucide/0.468.0/icons/table-properties.svg) — Hierarchy: Values |
| `history_icon` | History |
| `search_icon` | Search |
| `audit_icon` | Scroll Text |
| `download_icon` / `upload_icon` | Download / Upload |
| `plus_icon` | Plus |
| `lock_icon` | Lock |
| `circle_alert_icon` / `circle_check_icon` | [Circle Alert](https://raw.githubusercontent.com/lucide-icons/lucide/0.468.0/icons/circle-alert.svg) / [Circle Check](https://raw.githubusercontent.com/lucide-icons/lucide/0.468.0/icons/circle-check.svg) — Import mapping status |
| `info_icon` | Info |
| `close_icon` | X |
| `key_icon` | Key Round |
| `navigation_icon` | Panel Left Close / Panel Left Open |

The Select component (`static/js/select.js`) builds four of these Lucide `0.468.0` glyphs in
script, with the same geometry and attributes: Chevron Down (trigger), Check (chosen option),
Search and X (search field). `static/css/components/select.css` uses the same Chevron Down
geometry as a data URI for the closed select without JavaScript.

`key_icon` is tinted with the accent colour through the `.key-icon` rule. See
`LICENSE-Lucide.txt` for the upstream ISC/MIT notices.

The two Hierarchy action glyphs above retain the exact SVG geometry from the linked
official `0.468.0` source files. Their macro wrappers follow the existing interface icons;
CSS controls the displayed dimensions. No package or runtime request is added.

`network_icon` retains the exact official `0.468.0` geometry, which also matches the
`network` symbol in the vendored `1.46.0` dataset sprite. Hierarchy navigation and
the default hierarchy dataset icon therefore use the same glyph.

### Custom Hierarchy action icon

| Macro | Custom asset |
| --- | --- |
| `node_add_child_icon` | `node-add-child-open` — Hierarchy: Add child |

`node-add-child-open` is original refrncr artwork supplied by the product owner in
**Hierarchy: rij-acties als icons (28 sep 2026)**, section 3. It is not a Lucide asset.
The supplied parent-node rectangle, branch hook and open plus geometry are retained
exactly on a 24×24 grid with stroke width 2 and round caps/joins. This is a project
asset; no separate third-party licence was supplied with it.

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
- `refrncr-wordmark-reversed.svg`: dark-theme navigation, mobile bar, setup and login wordmark.
- `theme-preview-light.svg`, `theme-preview-dark.svg`, `theme-preview-system.svg`: REF-195 project artwork for Appearance settings.

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
