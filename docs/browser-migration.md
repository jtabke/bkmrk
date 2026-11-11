# Move browser bookmarks into bm

`bm import` reads Netscape bookmarks HTML, not a browser profile, Firefox JSON backup,
Safari ZIP archive, or ordinary pipeline JSON. Export from the desktop browser first;
menu labels vary by browser version. No extension, browser database access, or network
request is needed by `bm`.

## Export recipes

### Chrome

Open **Menu → Bookmarks and lists → Bookmark manager** (or `chrome://bookmarks`). In the
manager's menu, choose **Export bookmarks** and save the HTML file.
[Chrome export instructions](https://support.google.com/chrome/answer/96816?hl=en).

### Firefox

Open **Bookmarks → Manage Bookmarks**. In the Library window choose
**Import and Backup → Export Bookmarks to HTML…**. Do not choose **Backup…**, which
produces Firefox-specific JSON rather than HTML.
[Firefox export instructions](https://support.mozilla.org/en-US/kb/export-firefox-bookmarks-to-backup-or-transfer).

### Safari on macOS

On versions with **File → Export → Bookmarks…** (or **Export Bookmarks…**), save the HTML
file. Newer Safari versions may instead offer **File → Export Browsing Data to File**,
which produces a ZIP archive: select bookmarks only, extract the archive locally, and
use the bookmarks HTML file inside, not the ZIP. If your version does not provide a
bookmarks HTML file, transfer bookmarks to another desktop browser and export HTML there.
Do not export passwords or other browsing data just to migrate bookmarks. Safari's
browsing-data exports are not encrypted; protect and remove temporary exports appropriately.
[Safari export instructions](https://support.apple.com/guide/safari/ibrwebf10132/mac).

### Edge

Open **Favorites** (`edge://favorites`). In the Favorites menu choose **Export favorites**
and save the HTML file. Do not copy the internal `Bookmarks` profile file.
Microsoft also documents [HTML favorites interchange](https://support.microsoft.com/en-us/edge/import-your-favorites-and-passwords-in-microsoft-edge).

## Preview in a disposable store

The following examples use a POSIX shell and a temporary directory, never your default
store. On Windows, use a fresh temporary directory and pass its path with `--store`.
Replace `bookmarks.html` with your export's path:

```sh
work=$(mktemp -d)
bm --store "$work/store" import bookmarks.html --dry-run
bm --store "$work/store" import bookmarks.html
bm --store "$work/store" list
bm --store "$work/store" doctor
bm --store "$work/store" export -o "$work/backup.json"
```

Preview creates no files or directories in the destination store. Actual import creates
the store if necessary. Inspect the results before importing into your everyday store;
back up an existing store first. Keep the browser export until you have verified the move.
Browser import is best-effort, not a transaction; an I/O failure can leave earlier entries
imported. It does not delete existing files, and it does not synchronize browser bookmarks.

Folder hierarchy becomes store folders. Supported URLs and titles are imported; browser
creation dates are used when available. Do not expect browser-specific icons, descriptions,
reading status, or sync metadata to survive. Browser HTML is not a lossless bm backup.

## Reproducible preview counts

This small fixture shows two new destinations, one repeated destination, and one
unsupported URL. It does not require a real browser export:

```sh
work=$(mktemp -d)
cat > "$work/example.html" <<'HTML'
<!DOCTYPE NETSCAPE-Bookmark-file-1>
<DL><p>
<DT><A HREF="https://example.com/guide">Guide</A>
<DT><A HREF="https://example.com/news">News</A>
<DT><A HREF="https://example.com/guide">Guide again</A>
<DT><A HREF="javascript:alert(1)">Not importable</A>
</DL><p>
HTML
bm --store "$work/store" import "$work/example.html" --dry-run
```

Expected stdout:

```text
Would add 2, replace 0, skip existing 1; invalid 1.
No files written.
```

The unsupported URL also produces a stderr diagnostic:

```text
bm: skipped 1 entries with disallowed URL scheme
```

**Skipped-existing is not a separate normalized-URL duplicate count.** It counts destination
collisions, including repeated destinations within the input. The same URL in different
folders can create separate entries. URL variants can also create separate paths. Counts
are record actions, not unique files; `--force` changes collisions into replacements, and
later records can replace earlier ones. Do not use it merely to resolve duplicate URLs.

After importing, use `bm --store "$work/store" doctor` to report equivalent URLs and
`bm --store "$work/store" dedupe --dry-run` to inspect possible merges. Dedupe is an
explicitly destructive operation; inspect its metadata/notes policy and make a complete
backup before running it without `--dry-run`.

For exact bm-to-bm transfers, use [complete backup and restore](backup-format.md), not
`export json`, JSONL, or Netscape HTML. See the [compatibility policy](compatibility.md)
for what parsed rewrites and backups preserve.
