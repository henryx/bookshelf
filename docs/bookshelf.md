# Bookshelf

This is the bookshelf application.

# Features

## General

Frontend is developed using HTMX library as PWA, to permit to install on mobile devices.

Decisions:

 - Data access uses `firebird-driver` with literal SQL queries; no ORM.
 - The schema is created and upgraded by `init-db`, which adds missing tables, columns, constraints and indexes and
   is safe to rerun. There is no migration framework or version table: a change to an existing table is added as its
   own `ALTER TABLE` / `CREATE INDEX` statement and never edited into the table's original DDL (see README, "Schema
   changes"). `init-db` cannot rename, retype or drop objects.

## Authentication

### Technical details

Data for login are email and password. They are stored in a database table called USERS. Password is hashed with Argon2id
using a random per-password salt (e.g. `argon2-cffi` `PasswordHasher`); the salt and parameters are stored inside the
hash string, so no separate salt column is needed. Users can be two roles: regular and admin
(column `ROLE`, values `REGULAR` and `ADMIN`, enforced by a `CHECK` constraint). Admin role permit to user
to made admin operations such add or delete new users.

### Login page

When user open for the first time the webapp, a login page is shown. If login and password are correct, main page is
shown, otherwise login page show an error. A persistent cookie is saved to permit user to navigate in the webapp without
repeat login. If cookie is expired, or user made a logout action, next page showed is login page.

Decisions:

 - The persistent login cookie lasts 30 days. Cookies are `Secure`, `HttpOnly` and `SameSite=Lax`.
 - Email addresses are case-insensitive: stored lowercase and trimmed, and unique. The database enforces both
   (`UNIQUE` on `EMAIL`, plus the `CHK_USERS_EMAIL_NORMALISED` check), so even rows written outside the app cannot
   create two accounts differing only in case or spaces.
 - Wrong password, unknown email and locked account all show the same error, "Invalid email or password", so the
   page does not reveal which accounts exist.
 - Locking a user ends their open sessions on the next request.
 - Resetting a user's password ends their open sessions (including the persistent login cookie) on the next request.
 - Failed logins are limited per email and client IP together, and per IP. After 5 failed logins for an email from
   one IP within 15 minutes, that IP's logins for that email are refused ("Too many failed login attempts"); after
   20 failed logins from one IP across all emails, all logins from that IP are refused. Blocks last until the
   failures are older than 15 minutes. Because the email limit is per IP, an attacker cannot lock the real user out:
   their failures only block their own IP. The trade-off is that an attacker using many IPs gets 5 guesses per IP
   per account; Argon2 hashing keeps each guess slow. Unknown emails are limited the same way, so a block does not
   reveal which accounts exist. Each attempt is recorded as a failure before its password is checked, so attempts
   sent at the same moment count each other and no more than the limit get through. Refused attempts are not
   counted, and a successful login clears that email's failures from that IP. Failures are stored in the
   `LOGIN_FAILURES` table with the client IP (see `APP_TRUSTED_PROXIES` behind a reverse proxy, or every client
   shares the proxy's IP). The CLI is not limited.
 - Expired failures (older than the block window) are deleted in two places. Each login attempt deletes only the
   expired failures of its own email and IP: deleting every expired row on each attempt made concurrent logins
   delete the same rows, and Firebird failed all but one with an update conflict (an error page for the user).
   Rows for an email and IP that never try again are left behind, so the `prune-login-failures` CLI command
   deletes all expired failures and is meant to run periodically (e.g. hourly from cron); without it the table
   only grows. It needs no admin credentials, like `init-db`, since it removes only rows that no longer count.
   Known limitation: concurrent attempts for the same email and IP, or a prune run at the same moment as a login
   that deletes the same rows, can still hit the update conflict. That one request fails (or the prune run, which
   the next run catches up on); this is rare enough to be accepted.
   All limits are configurable (`APP_LOGIN_MAX_FAILURES`, `APP_LOGIN_MAX_IP_FAILURES`, `APP_LOGIN_BLOCK_MINUTES`;
   see README); 5, 20 and 15 are the defaults.
 - Emails must be valid addresses (checked with `email-validator`, no DNS lookup), on the login form and when
   creating users with `create-user` / `create-admin`, so every account created can log in. Reserved and LAN-style
   domains such as `user@bookshelf.local` or `user@nas` are rejected. Spaces around the email are ignored. An
   invalid email on the login form is refused before the credentials are checked, so it is neither counted as a
   failed login nor logged. Other CLI commands accept any email, so older accounts can still be managed.
 - A user's email cannot be changed. The email is the login, so an account with a wrong or invalid email is deleted
   (`delete-user`) and created again with the right one.
 - Failed and blocked logins are logged with the email and the client IP, like the CLI logs emails for every admin
   operation. Only valid emails reach the log, so a password typed into the email field is not logged.

### First run

When the database has no schema (no `USERS` table) or no admin, every request except static files is redirected
to a setup wizard (`/setup`). The wizard asks for an email and a password (entered twice), creates any missing
schema (`init_db`), creates the admin and logs them in. Once an admin exists the wizard returns 404.

Decisions:

 - The wizard also appears when the schema exists but no admin does (e.g. `init-db` was run but not
   `create-admin`), since the app cannot be used from the web without an admin.
 - The wizard is open to the first visitor, with no setup token. Known risk: if the app is reachable by others
   before setup is finished, anyone can create the admin. The administrator must finish setup first, or use the
   CLI (`init-db`, `create-admin`), which stays available.
 - The check runs on requests, not when the app starts: every gunicorn worker must notice when setup is done,
   and CLI commands should not query the database just to start. Each process caches "setup done" once it has
   seen an admin, so after setup the check costs nothing.
 - The wizard checks again for an admin just before creating one, and sends the visitor to the login page if
   another visitor finished first. Like `create-admin`, two submissions at the very same moment can still both
   succeed (two admins); this is accepted for the same reason.
 - The email follows the login form's rules. An email that already belongs to a (regular) user is refused.

### Admin area

Admin area is accessible only to admin users. In this area, are shown all users stored in the system. An admin can be:
 - create and delete an user.
 - promote an user as administrator.
 - lock an user.
 - reset the password for the user.

Status: the web admin area is not built yet. These operations are available as CLI commands (see README):

| Operation | CLI command |
|---|---|
| Create the first admin | `create-admin` (no admin credentials needed; refused once any admin exists, locked or not) |
| Create a regular user | `create-user` |
| List users | `list-users` |
| Promote / demote a user | `promote-user`, `demote-user` |
| Reset a password | `reset-user-password` (the admin sets the new password; no email is available) |
| Lock / unlock a user | `lock-user`, `unlock-user` |
| Delete a user | `delete-user` (asks for confirmation) |

Every CLI operation except `create-admin` requires valid admin credentials (correct password, `ADMIN` role, not
locked) and is logged with the admin who performed it. An admin cannot lock, demote or delete their own account.

Because every admin operation needs an active (not locked) admin, and no admin can lock, demote or delete
themselves, the admin performing an operation always remains an active admin. The CLI therefore can never leave
the system without an active admin, so the last admin needs no separate protection.

Known limitation: two `create-admin` runs at the same moment can both find no admin and both succeed, leaving two
admins. Firebird 3 cannot enforce "only one admin" with a constraint, and `create-admin` is run by hand once at
setup, so this is accepted rather than guarded with an extra table.

## Books

Status: not built yet. The questions below need an answer before the book tables are designed. Suggested defaults
are what would be built if the question stays open; each answer moves into a "Decisions" list here.

Open questions:

1. **Ownership.** Is there one shared library that every user sees, or does each user have their own books?
   - Shared: one catalogue; users differ only in what they may do (see 2).
   - Per user: each book belongs to a user; others cannot see it.
   - Suggested default: shared, since a household or club library is the likely use.
2. **Who may change books.** In a shared library, may regular users add, edit and delete books, or only admins?
   Suggested default: every user may add and edit; only admins may delete.
3. **Fields.** Which fields does a book have, and which are required?
   - Candidates: title, subtitle, authors, ISBN, publisher, publication year, language, edition, page count,
     cover image, description, notes, date added.
   - Suggested default: title required; authors, ISBN, publisher, year, language and notes optional.
4. **Authors.** Is the author a free-text field, or are authors their own records shared between books (so all
   books by one author can be listed, and a misspelling fixed once)? Can a book have several authors?
   Suggested default: author records, several per book, in order.
5. **Classification.** Are books grouped by genre, tags, series (with volume number), or shelf/location in the
   house? One of these, several, or none at first? Suggested default: free tags only.
6. **Copies.** Is a book a title (one record however many copies), or a physical copy (two copies are two
   records)? This matters if the same book can be owned twice or lent out. Suggested default: one record per
   title, with a copy count.
7. **Loans.** Should the app track who borrowed a book and when it is due back? To users of the app only, or also
   to people without an account (a name typed in)? Suggested default: not in the first version.
8. **Reading status.** Does each user track their own status per book (to read, reading, read) or a rating?
   Suggested default: not in the first version.
9. **ISBN lookup.** Should adding a book fill in its details from an online service by ISBN (e.g. Open Library)?
   That means outgoing internet requests from a self-hosted app, and handling the service being down. Scanning
   the barcode with the phone camera would come with it (PWA). Suggested default: manual entry first, lookup
   later.
10. **Covers.** If books have covers: uploaded files stored where (database or disk, and how big), or only a
    link to an online image? Suggested default: no covers in the first version.
11. **Duplicates.** Should the app refuse or warn about a second book with the same ISBN, or the same title and
    author? Suggested default: refuse the same ISBN; allow books without one.
12. **Deletion.** When a book is deleted, is it removed for good, or kept as deleted so it can be restored (and
    loans and history stay readable)? Suggested default: removed for good, after a confirmation.
13. **Import and export.** Is there an existing list of books to import (CSV, spreadsheet, another app's
    export)? Should the library be exportable as a backup? Suggested default: CSV export only.

## Main page

Status: placeholder. After login it shows only the user's email and a logout button.

Open questions:

1. **Content.** Does the main page list the books directly, or is it a dashboard (recently added, currently
   lent out, search box) with the list on its own page? Suggested default: the book list, with a search box.
2. **Search.** Which fields does the search cover: title, author, ISBN, tags, notes? Is one search box enough,
   or are filters needed (by author, tag, language)? Suggested default: one box over title, author and ISBN.
3. **Sorting.** What is the default order (title, author, date added), and can the user change it? Suggested
   default: by title, changeable to author or date added.
4. **Size.** Roughly how many books will the library hold: hundreds or many thousands? This decides between
   showing everything at once and paging or infinite scroll. Suggested default: pages of 50.
5. **Layout.** On the phone (PWA), a list of rows or a grid of covers? Depends on question 10 under Books.
   Suggested default: a list.
6. **Admin area link.** When the web admin area is built, is it reached from the main page (a link visible only
   to admins)? Suggested default: yes.
