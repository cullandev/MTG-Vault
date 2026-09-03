# Getting started

A walk from nothing to a running MTG Vault on your own machine, and then a tour
of what to do with it. This page assumes you have never run a Docker project
before. If you have, [README.md](README.md) says the same things more tersely.

Everything runs on one computer on your home network. Nothing is sent to a
cloud service; the only outside traffic is card data and prices from Scryfall
and the other public card APIs.

---

## Part 1 — Setting it up

### What you need

- **A computer that stays on.** Windows, macOS or Linux. The vault is a web
  server; you use it from a browser on that machine, other desktops, and your
  phone. Give it about 2 GB of disk for the app, images and card database,
  plus 4 GB more if you turn on AI games later.
- **Docker.** On Windows or macOS install
  [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start
  it. On Linux install Docker Engine with the compose plugin. To check it is
  working, open a terminal and run:

  ```bash
  docker compose version
  ```

  If that prints a version, you are ready.
- **Git**, to download the code. [git-scm.com](https://git-scm.com/downloads)
  on Windows or macOS; your package manager on Linux.
- **A phone on the same Wi-Fi**, if you want to scan cards with its camera.
  Optional: everything else works from a desktop browser.

### Step 1 — Download the code

Open a terminal (PowerShell on Windows, Terminal on macOS) in the folder where
you keep projects, and run:

```bash
git clone https://github.com/cullandev/MTG-Vault.git
```

```bash
cd MTG-Vault
```

Every later command in this guide is run from inside that folder.

### Step 2 — Write your settings file

The app reads its settings from a file named `.env`. Start from the example:

```bash
cp .env.example .env
```

(On Windows PowerShell, `copy .env.example .env` does the same.)

Open `.env` in any text editor. Four lines need your attention; leave the rest
alone for now.

| Setting | What to put there |
| --- | --- |
| `LAN_IP` | Your computer's address on your home network. Windows: run `ipconfig` and use the "IPv4 Address" of your Wi-Fi or Ethernet adapter. macOS: System Settings → Network. Linux: `ip a`. It looks like `192.168.1.50`. In your router, reserve this address for the machine so it does not change. |
| `LAN_HOSTNAME` | Your computer's name, for reaching it from other desktops by name. Windows shows it under Settings → System → About. Must not end in `.local`. |
| `SECRET_KEY` | A long random string that signs sessions. Generate one with `openssl rand -hex 32`, or if you have Python, `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `APP_PASSWORD` | The password the vault is created with. It is used once, on first start; change it later from inside the app. |

**About the login.** The example file ships with `AUTH_DISABLED=true`, which
means the app opens without asking for a password. That is the right choice
for one person on a private home network, which is what this project was
built for. If other people use your network, or you ever expose the machine
to the internet, set it to `false` and the password is required.

### Step 3 — Start it

```bash
docker compose up -d --build
```

The first run builds the app image, which takes several minutes and a few
hundred megabytes of downloads. When the command returns, check that both
containers are up:

```bash
docker compose ps
```

You should see `app` and `caddy` with a status of `running` (the app shows
`healthy` after a minute). If something is wrong, the logs say what:

```bash
docker compose logs --tail 50 app
```

The stack starts again by itself whenever Docker starts, so after a reboot
there is nothing to do.

### Step 4 — Open it

In a browser on the same machine, go to **`https://localhost`**, or from
another computer on your network, **`https://<LAN_IP>`** using the address
you put in `.env`.

The browser will warn that the connection is not private. That is expected:
the vault makes its own certificate, signed by its own private authority,
because there is no public domain to get a real one for. On a desktop, click
through the warning ("Advanced" → "Proceed"). On a phone you will install the
certificate properly in Step 6, because the camera refuses to work behind a
warning.

You are in, but the vault has no cards yet.

### Step 5 — Load the card database

The vault keeps its own copy of every Magic card ever printed, from Scryfall.
Downloading it takes about half a gigabyte and a few minutes:

```bash
docker compose exec app python -m app.cli import-bulk
```

Wait for it to finish. After this, searching, adding cards, decks and prices
all work. The import repeats itself every Sunday night to pick up new sets,
and skips the download when nothing has changed.

**Optional, for the scanner.** The phone scanner recognises cards mostly by
their artwork, using a fingerprint of every printing. Building that index
fetches a small image per printing and takes a few hours on a first run, in
the background:

```bash
docker compose exec -d app sh -c 'python -m app.cli build-hashes > /data/logs/hash_index.log 2>&1'
```

You can keep using the vault while it runs, and stop and restart it freely.
The scanner works before it finishes, just less confidently: it falls back to
reading the card's name and collector number.

### Step 6 — Trust the certificate on your phone

Skip this if you will not scan cards. Otherwise it is required: browsers only
allow camera access on a fully trusted `https://` site.

First, on the computer, copy the vault's root certificate to where the app can
serve it:

```bash
mkdir -p ./data/ca && cp ./data/caddy/data/caddy/pki/authorities/local/root.crt ./data/ca/root.crt
```

Then on the phone, open **`https://<LAN_IP>/ca.crt`**, accept the browser's
warning this one time, and download the file.

**iPhone or iPad**

1. Open the downloaded file. iOS says a profile was downloaded.
2. Settings → General → VPN & Device Management → tap the profile → Install.
3. Settings → General → About → Certificate Trust Settings → turn on the
   switch for "Caddy Local Authority".

Step 3 is the one everyone misses. Without it, Safari still refuses the site.

**Android**

Chrome saves the file to your Downloads. Then: Settings → Security →
Encryption & credentials → Install a certificate → CA certificate → choose
`ca.crt`. The exact menu path varies by manufacturer and Android version
(Samsung puts it under Security and privacy → Other security settings →
Install from device storage); searching Settings for "CA certificate" finds
it on any of them. Android warns that a third party could monitor your
traffic; that warning is describing your own certificate, and is expected.

Use Chrome on Android, not a manufacturer's own browser or Firefox: Chrome is
the one that trusts a certificate you installed this way and offers the
install-as-app prompt below.

Now open **`https://<LAN_IP>`** on the phone. No warning, and the camera will
work. To make it feel like an app, use **Share → Add to Home Screen** in
Safari, or in Chrome the **⋮** menu → **Install app** (older versions say
**Add to Home screen**). It then opens full screen with its own icon.

### Step 7 — Optional: AI games

The vault can play real games of Magic against a computer opponent, and run
your decks against tournament decks overnight, using the open-source Forge
rules engine. This is a separate 4 GB container, off by default.

To turn it on, set `ENABLE_FORGE=true` in `.env`, then start the stack with
the `battles` profile:

```bash
docker compose --profile battles up -d --build
```

The first build downloads Forge and takes a while. From then on, use that
same command instead of the plain `docker compose up`, so the third container
is included. The **Battles** and **Arena** pages explain the rest.

---

## Part 2 — Using it

The vault is organised as pages along the top (desktop) or bottom (phone).

### Getting your cards in

There are three ways, and you can mix them.

- **Import a spreadsheet.** If you already track your collection in Moxfield,
  Archidekt or Deckbox, export it as CSV and use **Import**. Choose the file
  and press **Preview**: the vault shows exactly what it will add and lists
  anything it could not match, and nothing is written until you press
  **Import for real**. The whole import is one entry in **History**, so it can
  be undone in one click.
- **Add by hand.** **Add** is a search box: type a name, pick the printing,
  set the quantity. Good for a handful of cards.
- **Scan with your phone.** Open **Scan** on the phone, put a card on a dark,
  plain surface, and hold the camera over it. When three readings agree, the
  card locks in with a sound and a buzz, and the count and value at the bottom
  tick up. The bottom bar sets foil, condition and where you store it. Anything
  the scanner cannot read falls back to a picker or the search box.

### Looking at what you own

- **Home** is the dashboard: totals, value over time, recent additions and
  the cards whose prices moved most.
- **Library** is every card you own, with filters, sorting and a saved view.
  Click a card for its page: printings, prices over time, and which decks use
  it.
- **Sets** shows your collection by set, with how complete each one is.
- **History** is the audit log. Every change to the collection is recorded and
  can be undone from here, including whole imports.
- **Buy list** is your wishlist: cards you want, in priority order, with
  what they cost now.

### Building and playing decks

- **Decks** holds your decks. Build one by searching your collection, and the
  vault checks legality for the format as you go, allocates physical cards so
  the same copy is not in two decks at once, and gives it a power rating and
  Commander bracket.
- **Meta** builds a deck for you from tournament results. Pick an archetype;
  the vault takes what the winning lists have in common and fills it from the
  cards you own, and the result is always legal.
- **Suggested** finds decks hidden in your collection: cards that combo or
  work together which you already own but have not put in a deck.
- **Battles** (needs Step 7) runs AI-versus-AI games between your decks, and
  a weekly gauntlet that pits them against the current tournament meta so you
  can see which of your decks actually wins.
- **Arena** (needs Step 7) is where you play. The start panel has two seats,
  **You play** and **The AI plays**: pick your deck, then an opponent of the
  same format. Opponents include real tournament lists: the week's leading
  cEDH decks arrive on their own after the Tuesday meta job, or press
  **Pull top decks** to fetch them now; add `mtgo` to `META_SOURCES_ENABLED`
  in `.env` and Modern and Standard Challenge winners come too. Choose the
  AI's personality (Forge's Default, Cautious, Reckless or Experimental) and,
  if you want a harder game, **Deeper thinking**, which is slower. Pick a
  playmat, type the name the table should call you, and press **Play**. Click
  cards to play them, click attackers to declare them, and use the buttons for
  everything else. Space passes, Enter ends the turn, Z undoes, Escape cancels.
  The turn track along the top shows where the game is; click a step to make
  the game stop there. When the AI casts a spell it is shown large in the
  middle of the table, and every card the log mentions can be hovered. The
  computer plays at a pace you can watch; tick **Fast game** if you would
  rather it did not wait.

### Keeping it healthy

- **System** shows the database, the last jobs, backups and settings, and has
  the buttons for a backup on demand and a full export.
- **Backups** happen every night into `data/backups/`. Set `BACKUP_MIRROR_DIR`
  in `.env` to a second drive or NAS so the backups do not live on the same
  disk as the database.
- **Everything is in the `data` folder** next to the code: the database, card
  images, downloads, logs and backups. Copy that folder and you have copied
  the whole vault.

---

## Day to day

**Updating to a newer version:**

```bash
git pull && docker compose up -d --build
```

(Add `--profile battles` if you use AI games.) Database upgrades run by
themselves on the next start.

**Stopping and starting:**

```bash
docker compose stop
```

```bash
docker compose start
```

**Changing the password:**

```bash
docker compose exec app python -m app.cli set-password
```

**Restoring a backup:** stop the app, copy the backup over the database, and
start again:

```bash
docker compose stop app && cp ./data/backups/mtgvault-<STAMP>.db ./data/mtgvault.db && rm -f ./data/mtgvault.db-wal ./data/mtgvault.db-shm && docker compose start app
```

### If something goes wrong

| Symptom | What it usually is |
| --- | --- |
| `docker compose` says it cannot connect | Docker Desktop is not running. Start it and wait for the whale icon to settle. |
| Port 443 or 80 is already in use | Another web server on the machine. Stop it, or change the ports in `docker-compose.yml`. |
| The phone shows a certificate warning | Step 6 was not completed, most often the trust switch on iOS. |
| The camera will not open on the phone | Same cause: the site is not fully trusted, or you opened it over `http://`. |
| Searching finds no cards | Step 5 has not run yet, or is still running. |
| The Arena says a game is already being watched | A game is still running in the sidecar. Open the Arena and it attaches to it; press **Stop** to end it. |
| Something else | `docker compose logs --tail 100 app` almost always says what. |
