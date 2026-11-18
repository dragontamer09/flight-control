# Flight Control UI

Use an airfield briefing style. A deep blue header and amber runway marker identify Flight Control. Condensed headings mark the document hierarchy. A short contract index links to the sections students look up during implementation and testing.

## Tokens

- Use `--bg` for the page, `--panel` for cards, and `--panel-2` for inset surfaces and table headers. Use `--line` for separators and `--control-line` for input borders.
- Use `--ink` and `--muted` for text. Use `--accent` and `--accent-ink` together for primary actions. Reserve `--deck-*` and `--signal` for navigation surfaces.
- Use `--display` (Barlow Semi Condensed) for headings, `--sans` (IBM Plex Sans) for reading and controls, and `--mono` (IBM Plex Mono) for code and identifiers.
- Use the 16px phone gutter defined by `--gutter`. The theme follows `prefers-color-scheme` and accepts `data-theme="light"` or `data-theme="dark"` on `html`.

## Components

- Use `main.narrow` for team and report layouts and `main` for wide layouts. Use `.card` for panels and `.card.table-wrap` for tables that can scroll within a panel. Mark numeric cells with `.num`.
- Use `.field` for a label and its control, `.buttons` for action groups, `.inline` for inline forms, and `.ghost` for secondary buttons. Use `.btn` for links that act as buttons.
- Use `.stats`, `.label`, and `.stat` for metric groups and `.kv` for definition lists. Use `.msg` and `.msg.err` for feedback, `.pill` for badges, and `.ok`, `.no`, or `.warn` for status text and badge variants. Include a text label with each status.

## Page tasks

Put each page's CSS in its marked block at the end of `style.css`. Scope page-specific selectors to that page's main element and reuse the shared tokens and components. Keep form actions, template data, and live updates connected. Check both themes at 390px and keep wide tables and code examples inside their own scroll regions.
