# Third-party UI assets

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
the text around it. They are the following icons from the official Lucide release `0.468.0`,
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
