# Bookshelf

This is the bookshelf application.

# Features

## General

Frontend is developed using HTMX library as PWA, to permit to install on mobile devices.

Decisions:

 - Data access uses `firebird-driver` with literal SQL queries; no ORM.
 - The schema is created by `init-db`, which creates missing domains, tables, constraints and indexes and is safe
   to rerun. Tables are created with their columns only; every constraint is added separately with a name of our
   own (`PK_USERS`, `UQ_USERS_EMAIL`, …) instead of one Firebird generates.
   Until the first release is tagged the schema is not final: tables are changed by editing their `CREATE TABLE`
   in place, with no migration steps, and development databases are recreated. Once a release is tagged, changes to
   released tables will need upgrade steps for existing databases (see README, "Schema changes").

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
 - `LOGIN_FAILURES` has no primary key. Its `ID` references the user the failed email belongs to
   (`FK_LOGIN_FAILURES_USER`, `ON DELETE CASCADE`, so deleting a user deletes their failures) and is NULL for an
   unknown email, which is still recorded so that a block does not reveal which accounts exist. Without a
   per-attempt key, a refused attempt removes its row by deleting one row with the same email, IP and time; such
   rows are indistinguishable, so it does not matter which. Concurrent refused attempts may pick the same row:
   Firebird fails all but one with an update conflict, and those retry (up to 10 times) and then see the row gone.
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
 - New passwords must be at least 12 characters long; that is the only rule (no character classes, no maximum,
   spaces allowed), since length is what makes a password hard to guess. The length can be raised with
   `APP_MIN_PASSWORD_LENGTH` (e.g. to 15, the NIST SP 800-63B minimum for a password used alone) but not lowered:
   a value below 12, or one that is not a number, stops the app and the CLI from starting. It applies wherever a
   password is set: the setup wizard and the `create-admin`, `create-user` and `reset-user-password` commands,
   which ask again until the password is long enough. The model (`create_user`, `set_password`) enforces it too:
   callers pass the configured length, and a caller that omits it still gets 12, so no code path can go below the
   floor. Login does not check it, so existing shorter passwords keep working until they are reset.
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

The admin area is where admins manage the people who can use Bookshelf. Only admins can open it: a visitor who is
not logged in is sent to the login page, and a regular user is refused. An admin can:

 - See every user, with their email, role and status.
 - Create a user.
 - Make a user an admin, or an admin a regular user again.
 - Lock a user, so they can no longer log in, and unlock them.
 - Set a new password for a user. There is no email, so the admin chooses it and tells the user.
 - Delete a user for good.

**Opening it.** When an admin is logged in, an "Admin" button appears in the top bar next to their email; other users
do not see it. It opens the users list: a table with each user's email, role (regular or admin) and status (active or
locked).

**Editing a user.** Clicking an email opens the user's details in a modal dialog over the list. The email is shown but
cannot be changed: it is the login, so a wrong email means deleting the user and creating them again. The role, the
lock and a new password (typed twice; left empty, the current one is kept) are editable. Save applies the changes
together, and only if every field is valid; otherwise the dialog stays open, shows what is wrong and changes nothing.
Closing the dialog discards unsaved changes.

**Deleting a user.** The details dialog has a Delete button, which asks for confirmation.

**Creating a user.** A "Create user" button, on the right of the "Users" heading, opens a dialog asking for the
email and the password (typed twice). New users are regular users; an admin makes them admins from their details.

**Rules.** Emails and passwords follow the rules used everywhere else: a valid email not used by another user, and
a password of at least `APP_MIN_PASSWORD_LENGTH` characters (12 by default). An admin cannot demote, lock or delete
their own account, so at least one active admin always remains; they may change their own password and stay logged
in.

**Effects.** Changes apply from the user's next request: a locked user, or one whose password changed, is logged out
on every device, "remember me" cookie included, and a demoted admin loses the admin area. After a save, a creation
or a deletion the list reloads with a confirmation message, and the change is logged with the admin who made it.

Status: built at `/admin/users`.

Decisions:

 - Regular users get 403 rather than a redirect. The user is reloaded from the database on every request, which is
   what makes locking, demotion and password changes take effect at once.
 - Without JavaScript the admin area still works: an email link opens the details as a page (`/admin/users/<id>`),
   and a create form posted with errors comes back with its dialog already open.
 - Save writes role, lock and password in one transaction, after all fields are validated.
 - An admin's own details show role and status as text and have no Delete button; requests that would demote, lock
   or delete their own account are refused anyway. Changing their own password renews their session.
 - An error from the server, or no answer at all, closes any open dialog, so the error banner is not hidden behind
   it.

Known limitations:

 - Unlike the CLI, the admin area does not ask for the admin's password again before a change; it relies on the
   login session and CSRF protection.
 - The delete confirmation comes from htmx, so without JavaScript a user is deleted without asking. htmx is vendored
   with the app, so in practice it is always loaded.

The same operations are also available as CLI commands (see README):

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

Status: not built yet. The decisions below are settled; the book tables can now be designed.

Decisions:

 - The library is shared: there is one catalogue of books, and every user sees all of it.
 - Each user also has their own collection: the books from the shared catalogue that are theirs. A book a user
   adds goes into their collection automatically.
 - A user can add any book already in the catalogue to their own collection, and remove any book from it;
   removing it leaves the book in the catalogue. Users change only their own collection. Every user can see who
   owns each book and how many copies.
 - Every user, regular or admin, may add a book to the catalogue and edit any book in it, not only the books
   they added. Only admins may delete a book.
 - A book has a title (required) and optionally authors, ISBN, publisher, publication year, language and notes.
   Authors are a list of names, so a book can have several, in the order given.
 - Authors are their own records, shared between books: when a book is added or edited, each author is picked
   from the existing ones by name, or created if new. All books by one author can be listed, and a misspelled
   name is fixed once for every book.
 - Books are classified by free tags only: a book can have any number of tags, typed freely. There are no
   genres, series or shelf locations.
 - A book is a title: one record in the catalogue however many copies exist. The number of copies belongs to a
   user's collection: each user records how many copies of the book they own (at least one), so two users
   owning the same book is still one catalogue record.
 - Loans (who borrowed a book and when it is due back) are not tracked in the first version.
 - Reading status (to read, reading, read) and ratings are not tracked in the first version.
 - Books are entered by hand in the first version. Filling in a book's details from an online service by ISBN
   (e.g. Open Library), and scanning the barcode with the phone camera, may come later.
 - Books have no cover images in the first version.
 - A book whose ISBN is already in the catalogue is refused. Any number of books may have no ISBN, and books
   with the same title and authors are allowed.
 - A book can be deleted only while it is in no user's collection; otherwise deletion is refused. Deleting a
   book removes it for good (it cannot be restored) and asks for confirmation first.
 - The library can be exported as a CSV file. There is no import in the first version.

## Main page

Status: placeholder. After login it shows only the user's email and a logout button.

Decisions:

 - The main page is the book list, with a search box. There is no separate dashboard page.
 - One search box searches title, author and ISBN together. There are no separate filters (by author, tag or
   language).
 - The list is sorted by title by default; the user can sort it by author or by date added instead.
 - The list is shown in pages of 50 books.
 - On the phone, books are shown as a list of rows, not a grid (books have no covers; see Books).
