# Integration recipes

Shell examples below use a POSIX shell and your configured store. They can open a browser
or change bookmarks where indicated; use a disposable `bm --store PATH` store while
experimenting. JSON recipes need `jq`; launcher recipes also need the named launcher.
For clipboard helpers and shell completion, see [setup](setup.md).

**fzf launcher**

With `fzf` installed, no wrapper or `jq` dependency is required:

```bash
bm open
bm open --tag dev --print url
```

**Open the latest saved from a host**

```bash
id="$(bm list --host example.com --jsonl | head -1 | jq -r '.id')"
if [ -n "$id" ]; then
    bm open "$id"
fi
```

**Rofi launcher**

```bash
#!/bin/sh
choice="$(
    bm list --jsonl |
    jq -r '.id + "\t" + .title + " — " + .url' |
    rofi -dmenu -i -p "bm"
)"
if [ -n "$choice" ]; then
    bm open "$(printf "%s" "$choice" | cut -f1)"
fi
```

Save as `bm-rofi.sh`, make it executable (`chmod +x bm-rofi.sh`), and bind it to a hotkey. The
tab delimiter keeps IDs intact even when titles contain spaces; rofi shows the full title and URL
while `bm open` receives only the bookmark ID.

**dmenu launcher**

```bash
#!/bin/sh
choice="$(
    bm list --jsonl |
    jq -r '.id + "\t" + .title + " — " + .url' |
    dmenu -l 15 -i -p "bm"
)"
if [ -n "$choice" ]; then
    bm open "$(printf "%s" "$choice" | cut -f1)"
fi
```

You can adjust `-l 15` to change the number of visible rows. Because the script uses tabs between
the ID and the description, `cut -f1` reliably extracts the ID even when titles or URLs contain
spaces.

**Bulk tag HN links**

```bash
bm list --host news.ycombinator.com --jsonl |
jq -r '.id' |
while IFS= read -r id; do
    [ -n "$id" ] && bm tag add "$id" hn
done
```

**List bookmarks in a specific category**

```bash
bm list --path dev/python
bm search "framework" --path dev
```

**Explore directory structure**

```bash
bm dirs
bm dirs --json | jq
```

**Export → browser import**

```bash
bm export netscape > ~/Desktop/bookmarks.html
# Import that file in your browser’s bookmarks manager
```

**Sync with Syncthing**

For cross-device synchronization without Git, use [Syncthing](https://syncthing.net/) to sync your bookmark store:

1. Install Syncthing on all devices.
2. Add your bookmark store directory (`~/.bookmarks.d` or `$BOOKMARKS_DIR`) as a synced folder in Syncthing.
3. Configure devices to share the folder bidirectionally.
4. Syncthing will keep your bookmarks in sync across devices automatically.

**Auto-export for browser import**

To automatically export bookmarks to Netscape HTML for browser import:

```bash
#!/bin/bash
# auto_export.sh
bm export netscape > ~/bookmarks_auto.html
echo "Bookmarks exported to ~/bookmarks_auto.html. Import this file in your browser."
```

Run this script periodically or on demand to generate an up-to-date bookmark file for browser import.
