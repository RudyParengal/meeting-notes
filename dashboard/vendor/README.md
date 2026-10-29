# Bundled browser libraries

The dashboard loads these from this folder, never from a CDN, so it works offline and nothing outside can change what runs in the page.

| File | Library | Version | Licence | Why |
|---|---|---|---|---|
| `marked.umd.js` | [marked](https://github.com/markedjs/marked) | 18.1.0 | MIT — `LICENSE-marked.txt` | Turns the AI's Markdown notes into HTML |
| `purify.min.js` | [DOMPurify](https://github.com/cure53/DOMPurify) | 3.4.16 | Apache-2.0 or MPL-2.0 — `LICENSE-dompurify.txt` | Cleans that HTML before it is shown, so text in a transcript cannot run script in the page |

## Updating one

1. Download the new file from the project's official release (npm package `marked` → `lib/marked.umd.js`; `dompurify` → `dist/purify.min.js`).
2. Replace the file here and update the version above.
3. Run `node --check dashboard/app.js` and open a note in the dashboard: headings, lists and tables should still render.
