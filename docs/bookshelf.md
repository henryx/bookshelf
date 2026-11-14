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
 - Every response carries security headers: a Content-Security-Policy allowing only the app's own scripts,
   styles, fonts and images (no inline code, no plugins, forms posting only to the app) and forbidding frames,
   `X-Frame-Options: DENY` for older browsers, `X-Content-Type-Options: nosniff` and
   `Referrer-Policy: same-origin`. So no page can be framed by another site (clickjacking), and pages must not
   use inline `<script>`, `<style>` or `style=""`: htmx's own inline indicator styles are turned off
   (`includeIndicatorStyles`), their rules being in `style.css`. HSTS is set by the HTTPS proxy, not the app.

## Authentication

### Technical details

Data for login are email and password. They are stored in a database table called USERS. Password is hashed with Argon2id
using a random per-password salt (e.g. `argon2-cffi` `PasswordHasher`); the salt and parameters are stored inside the
hash string, so no separate salt column is needed. Users can be two roles: regular and admin
(column `ROLE`, values `REGULAR` and `ADMIN`, enforced by a `CHECK` constraint). Admin role permit to user
to made admin operations such add or delete new users.

A user also has an optional name and surname (columns `NAME` and `SURNAME`), shown in the top bar and the admin
area. Either may be missing; empty fields are stored as NULL, and the database keeps given ones trimmed and non-blank
(`CHK_USERS_NAME`, `CHK_USERS_SURNAME`). Each is up to 127 characters, like an author's.

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
 - Name and surname are optional everywhere: the setup wizard and the CLI (`create-admin`, `create-user`) do not ask
   for them; users fill them in from their account page, or an admin from the admin area.
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

 - See every user, with their email, name, role and status.
 - Create a user.
 - Set or change a user's name and surname.
 - Make a user an admin, or an admin a regular user again.
 - Lock a user, so they can no longer log in, and unlock them.
 - Set a new password for a user. There is no email, so the admin chooses it and tells the user.
 - Delete a user for good.

**Opening it.** When an admin is logged in, the account menu in the top bar (see "Account menu") has an "Admin" link;
other users do not see it. It opens the users list: a table with each user's email, name and surname, role (regular
or admin) and status (active or locked).

**Editing a user.** Clicking an email opens the user's details in a modal dialog over the list. The email is shown but
cannot be changed: it is the login, so a wrong email means deleting the user and creating them again. The name and
surname, the role, the lock and a new password (typed twice; left empty, the current one is kept) are editable. Save applies the changes
together, and only if every field is valid; otherwise the dialog stays open, shows what is wrong and changes nothing.
Closing the dialog discards unsaved changes.

**Deleting a user.** The details dialog has a Delete button, which asks for confirmation.

**Creating a user.** A "Create user" button, on the right of the "Users" heading, opens a dialog asking for the
email, the name and surname (optional) and the password (typed twice). New users are regular users; an admin makes them admins from their details.

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
| Set a user's name and surname | none: web only (the admin area, or the user's account page) |

Every CLI operation except `create-admin` requires valid admin credentials (correct password, `ADMIN` role, not
locked) and is logged with the admin who performed it. An admin cannot lock, demote or delete their own account.

Because every admin operation needs an active (not locked) admin, and no admin can lock, demote or delete
themselves, the admin performing an operation always remains an active admin. The CLI therefore can never leave
the system without an active admin, so the last admin needs no separate protection.

Known limitation: two `create-admin` runs at the same moment can both find no admin and both succeed, leaving two
admins. Firebird 3 cannot enforce "only one admin" with a constraint, and `create-admin` is run by hand once at
setup, so this is accepted rather than guarded with an extra table.

### Account menu

Status: built, in the top bar of every page after login.

The upper right corner of the top bar shows who is logged in: their name and surname (or their email, without a
name) next to a round avatar with their initials (or the email's first letter). On a phone only the avatar is
shown. Clicking it opens a menu with the name and the email, "Your account", "Your collection", "Admin" (admins
only) and "Log out".

Decisions:

 - The menu is a `<details>` element, so it opens and closes without JavaScript; with JavaScript a click elsewhere
   or Escape also closes it.
 - Initials are the first letters of the name and the surname that are given, upper case.

### Your account

Status: built at `/account`.

Every user can change their own name and surname, and their password. The email is shown but cannot be changed (see
Login page). Save applies the changes together, and only if every field is valid.

Decisions:

 - A new password needs the current one, so a session left open on a shared device cannot be used to take the
   account over. A wrong current password is refused and logged; it is not counted towards the login limits.
 - Changing the password ends the user's sessions on other devices ("remember me" cookie included), like an admin's
   reset, but keeps this one: the user stays logged in.
 - Role and lock state are not on this page, and saving it never changes them, even if an admin changes them in
   the meantime.
 - Empty name or surname fields clear them.

## Books

Status: tables created by `init-db` (`BOOKS`, `AUTHORS`, `BOOK_AUTHORS`, `BOOK_TAGS`, `COLLECTION_BOOKS`). Books
can be listed (see Main page), added (see "Adding a book") and kept in a collection (see "Your collection"); not yet
edited or deleted.

Decisions:

 - The library is shared: there is one catalogue of books, and every user sees all of it.
 - Each user also has their own collection: the books from the shared catalogue that are theirs. A book a user
   adds goes into their collection automatically.
 - A user can add any book already in the catalogue to their own collection, and remove any book from it;
   removing it leaves the book in the catalogue. Users change only their own collection. Every user can see who
   owns each book and how many copies.
 - Every user, regular or admin, may add a book to the catalogue and edit any book in it, not only the books
   they added. Only admins may delete a book.
 - A book has a title (required) and optionally authors, ISBN, publisher, publication year, volume and notes.
   A book can have several authors, in the order given; each author is a name and a surname.
 - Authors are their own records, shared between books: when a book is added or edited, each author is picked
   from the existing ones by name and surname, or created if new. All books by one author can be listed, and a
   misspelled name is fixed once for every book.
 - An author is a name and a surname, and that pair is unique: two different authors cannot share both, so
   picking an author is never ambiguous. Authors may share a surname (Charlotte and Emily Brontë). The name (the
   given name) may be empty, for authors known by one name ("Homer"); it is stored as '' rather than NULL so that
   uniqueness still holds. An author used by a book cannot be deleted; unused authors are kept until removed.
 - Books are classified by free tags only: a book can have any number of tags, typed freely. There are no
   genres, series or shelf locations. Tags are stored as text on each book, with no list of tags of their own.
 - The volume is free text ("2", "II", "3/4"), since volumes are numbered in many ways. Books have no language.
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
 - ISBNs are stored as 13 digits: hyphens and spaces are removed and an ISBN-10 is converted to its ISBN-13, so
   the same book typed either way is caught as a duplicate.
 - Titles, publishers, author names and surnames, and tags ignore case when compared and sorted ("Sci-fi" and
   "sci-fi" are one tag), but not accents ("Citta" and "Città" differ). All are trimmed; titles, surnames and
   tags cannot be blank. Empty optional fields (publisher, volume, notes, …) are stored as NULL.
 - Deleting a user removes their collection, which may leave books in no collection (and so deletable).
 - Sizes: title and publisher up to 255 characters, author name and surname up to 127 each, volume up to 32,
   tag up to 64, notes up to 4000. Name and surname are limited to 127 because the unique index on both must
   fit Firebird's index key limit (8 KB pages). Who added a book is not recorded.

### Adding a book

Status: built (`/books/new`, with suggestions from `/authors/suggestions` and `/publishers/suggestions`).

A round "+" button floats over the bottom-right corner of the main page. It opens a dialog with the book's
fields and the number of copies the user owns (1 by default); "Add book" saves it to the catalogue and to the
user's collection, and the list reloads with a confirmation. Without JavaScript the button opens the same form
as a page (`/books/new`).

Decisions:

 - Each author is a row of two fields, name and surname, in order. The first row has a "+" button that adds
   a row; each added row has a "−" button that removes it. Without JavaScript these buttons submit the form,
   and the server shows it again with the row added or removed, saving nothing.
 - The surname is required in a row with a name; the name may be left empty. Empty rows are skipped. The same
   author (name and surname, ignoring case) cannot be listed twice: the repeated row is marked and nothing is
   saved.
 - An author whose name and surname match an existing one (ignoring case) is that author, keeping its
   existing spelling. This holds even when someone else adds the same new author while the book is being saved:
   the book uses theirs. For this a book is saved in its own read-committed transaction; in the request's
   snapshot transaction neither the lookup nor the foreign key check would see an author committed after it
   started, and the save would fail.
 - Typing in an author's name or surname field lists, under the field, up to 10 existing authors whose name
   (or surname) starts with the text, ignoring case, shown as "Name Surname". Picking one fills both fields
   of the row.
 - The publisher field suggests the same way: up to 10 publishers already used in the catalogue that start
   with the text, ignoring case (spellings differing only in case are one publisher). Picking one fills the
   field.
 - Suggestions use the browser's own suggestion list (`<datalist>`): it needs little JavaScript and works with
   the keyboard and screen readers, but its look is the browser's. Without JavaScript the fields are plain
   inputs, with no suggestions.
 - Publisher and year share a row; the year field is narrow.
 - Tags are separated by commas. Blank tags are skipped, and a tag repeated (ignoring case) is kept once.
 - The ISBN must be a valid ISBN-10 or ISBN-13, check digit included, so a mistyped ISBN is caught; it is saved
   as ISBN-13. An ISBN already in the catalogue is refused with a message naming it. Under the message the
   form offers to add that book to the user's collection instead, with the copies typed in the form (a button
   posting the form to the collection; without JavaScript through `formaction`). If the book is already in their
   collection, the form says so, with its copies.
 - The publication year is between 1 and 9999; copies between 1 and 999.

Known limitations:

 - A repeated author is refused when the form is submitted, not while typing; the suggestions still list
   authors already in another row.
 - If two authors have the same full name split differently ("Anne Marie" + "Smith" and "Anne" +
   "Marie Smith"), picking either suggestion fills the row with the first one.

## Main page

Status: built at `/`, with the add-book button (see Books, "Adding a book").

Decisions:

 - The main page is the book list, with a search box. There is no separate dashboard page.
 - One search box searches title, author and ISBN together. There are no separate filters (by author or tag).
 - The list is sorted by title by default; the user can sort it by author or by date added instead.
 - The list is shown in pages of 50 books.
 - On the phone, books are shown as a list of rows, not a grid (books have no covers; see Books).
 - Each row shows the title, the authors ("Name Surname", in their order) and the publication year.
 - Under that, a book in the user's collection shows a stamp, "In your collection · 2" (the copies), linking to
   their collection; any other book an "Add to collection" button, which adds it with one copy.
 - Sorting by author uses each book's first author (surname, then name), then the title; books without
   authors come last. Date added sorts newest first.
 - The search ignores case and spaces around it. An author matches on name, surname or both ("Frank
   Herbert"). In ISBNs, hyphens and spaces are ignored, and an ISBN-10 finds the book stored with the
   matching ISBN-13.
 - With htmx, typing in the search box (after a short pause), changing the order and moving between pages
   reload only the list, and the address bar keeps the search, order and page so they can be bookmarked.
   Without JavaScript the search is a plain form and the page links are plain links.
 - A page number out of range shows the nearest page.

## Your collection

Status: built at `/collection`, linked from the account menu.

The books the user owns, from the shared catalogue: the same list as the main page (search, order, pages of 50),
showing only their books. Each row has the number of copies, editable, with "Save", and a "Remove" button.

Decisions:

 - The collection has its own page; the main page stays the whole catalogue.
 - Books are added to the collection from the main page's rows, or from the add-book form when the ISBN typed is
   already in the catalogue (see "Adding a book"). Adding a book already in the collection changes nothing and
   says so.
 - Removing a book takes one click, with no confirmation: it only leaves the collection, and can be added again
   from the main page (with one copy).
 - Copies are between 1 and 999, as when adding a book.
 - With htmx, the rows' buttons (add, save, remove) reload only the list, keeping its search, order and page,
   with a message above it. Without JavaScript they are plain forms, and the server redirects back to the list
   with the same search, order and page and the message shown on it.
 - "Date added" orders the collection by when each book entered the catalogue: the collection does not record
   when a book joined it.

Known limitations:

 - Saving copies or removing a book reloads the list, so the focus goes back to the top of the page.
