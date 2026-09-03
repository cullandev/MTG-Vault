# Security

## Threat model

MTG Vault is designed for **one person on a private home network**. It ships
with `AUTH_DISABLED=true` in `.env.example`, which means the app opens without
a password. That is deliberate for its intended deployment, and it is also why
the app must **not** be exposed to the internet, a shared network, or a tunnel
without first setting `AUTH_DISABLED=false` and choosing a strong
`APP_PASSWORD` and `SECRET_KEY`.

Even with authentication on, the app has a single account, no rate limiting
beyond what Caddy provides, and no hardening review for hostile networks.
Treat it as LAN-only software.

## What is in scope

- Authentication bypass when `AUTH_DISABLED=false`.
- Path traversal or arbitrary file read/write through imports, backups,
  exports, the image cache or the certificate route (`/ca.crt`).
- Anything that lets a crafted CSV, decklist, scan upload or external API
  response (Scryfall, EDHREC, Spellbook, edhtop16, MTGO, Moxfield) execute code or
  corrupt the database.
- Secrets leaking into logs, backups or exports.

## What is out of scope

- Attacks that require the app to be reachable from the internet, or a
  network the operator does not control.
- Denial of service against a single-user LAN instance.
- The Forge sidecar's own rules engine (report those upstream to Forge).

## Reporting

Use GitHub's private vulnerability reporting on this repository
("Security" tab → "Report a vulnerability"). If that is unavailable, open an
issue that says only that you have a security report and how to reach you;
do not put details in a public issue.

There is no bug bounty. Reports are answered on a best-effort basis by one
maintainer.
