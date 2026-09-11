# Third-party UI assets

All files in this inventory are served locally. The application makes no runtime request to
their upstream projects.

## Fonts

The application typefaces are shared with the refrncr website. Each weight is served as a Latin and a
Latin Extended subset, selected by `unicode-range`.

- Instrument Sans Medium 500 and SemiBold 600: `instrument-sans-{latin,latin-ext}-{500,600}-normal.woff2`,
  from the Fontsource package `@fontsource/instrument-sans` 5.3.0; licensed under
  `OFL-Instrument-Sans.txt`.
- IBM Plex Sans Regular 400, SemiBold 600 and Bold 700:
  `ibm-plex-sans-{latin,latin-ext}-{400,600,700}-normal.woff2`, from `@fontsource/ibm-plex-sans`
  5.3.0; licensed under `OFL-IBM-Plex-Sans.txt`.
- IBM Plex Mono Regular 400: `ibm-plex-mono-{latin,latin-ext}-400-normal.woff2`, from
  `@fontsource/ibm-plex-mono` 5.3.0; licensed under `OFL-IBM-Plex-Mono.txt`.

The following earlier typefaces are no longer referenced by `tokens.css`. They stay packaged until
their removal is approved:

- Montserrat Medium 500: `Montserrat-Medium.woff2`, from the official Montserrat
  source at commit `cc8daf2e7085006b9c112542fc82b58afc13521d`; licensed under
  `OFL-Montserrat.txt`.
- Inter Regular 400, SemiBold 600 and Bold 700: `Inter-Regular.woff2`,
  `Inter-SemiBold.woff2` and `Inter-Bold.woff2`, from the official Inter repository at commit
  `66647c0bbbe41a850d79d9c76fb13add3378940f`; licensed under `OFL-Inter.txt`.
- JetBrains Mono Regular 400: `JetBrainsMono-Regular.woff2`, from the official JetBrains Mono
  release `v2.304`; licensed under `OFL-JetBrains-Mono.txt`.

## Icons

The five `lucide-*.svg` files are the Download, Key Round, Plus, Settings 2 and Upload icons
from the official Lucide release `0.468.0`. The File Pen and Search icons from the same release are rendered inline by
the shared `file_pen_icon` and `search_icon` macros in `templates/components/icons.html`. The Panel Left Close and Panel Left Open icons from `0.468.0` are rendered inline by
`navigation_icon` in the same template. The Key Round colour is adapted to the REFRNCR palette. See
`LICENSE-Lucide.txt` for the upstream ISC/MIT notices.
