# Bookshelf

This is the bookshelf application.

# Features

## General

Frontend is developed using HTMX library as PWA, to permit to install on mobile devices.

Decisions:

 - Data access uses `firebird-driver` with literal SQL queries; no ORM.
 - The schema is created by `init-db`, which creates missing domains, tables, constraints and indexes, creates or
   replaces the PSQL functions (`BOOK_MATCHES`, the book lists' search), and is safe to rerun. Tables are created with their columns only; every constraint is added separately with a name of our
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

When user open for the first time the webapp, a login page is shown. If login and password are correct, the user's
collection is shown (see "Your collection"), otherwise login page show an error. A persistent cookie is saved to permit user to navigate in the webapp without
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
schema (`init_db`), creates the admin and logs them in, on their (empty) collection. Once an admin exists the wizard returns 404.

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
in. Deleting or demoting an admin is also refused when no other admin would remain ("… is the only admin left"),
which only two admins removing each other at the same moment could otherwise cause.

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
themselves, the admin performing an operation always remains an active admin. Two admins removing each other at the
same moment (from the CLI or the admin area) could still both succeed and leave no admin, and the next process
would then open the setup wizard to anyone (see "First run"). So deleting or demoting an admin first updates the
other admins' rows, unchanged, inside its transaction: two such removals at once conflict, and one fails with an
error instead of both succeeding. With no other admin left it is refused (`LastAdminError`).

Known limitation: two `create-admin` runs at the same moment can both find no admin and both succeed, leaving two
admins. Firebird 3 cannot enforce "only one admin" with a constraint, and `create-admin` is run by hand once at
setup, so this is accepted rather than guarded with an extra table.

### Account menu

Status: built, in the top bar of every page after login.

The upper right corner of the top bar shows who is logged in: their name and surname (or their email, without a
name) next to a round avatar with their initials (or the email's first letter). On a phone only the avatar is
shown. Clicking it opens a menu with the name and the email, "Your account", "Your collection", "Catalogue"
(the main page), "Authors", "Admin" (admins only) and "Log out". The "Bookshelf" title on the left leads to the
first page, the user's collection.

Decisions:

 - The menu is a `<details>` element, so it opens and closes without JavaScript; with JavaScript a click elsewhere
   or Escape also closes it.
 - Initials are the first letters of the name and the surname that are given, upper case.

### Your account

Status: built at `/account`.

Every user can change their own name and surname, and their password. The email is shown but cannot be changed (see
Login page). Save applies the changes together, and only if every field is valid. Under the form, the user exports
their collection to CSV or imports a CSV file into it (see "Collection CSV import and export").

Decisions:

 - A new password needs the current one, so a session left open on a shared device cannot be used to take the
   account over. A wrong current password is refused and logged; it is not counted towards the login limits.
 - Changing the password ends the user's sessions on other devices ("remember me" cookie included), like an admin's
   reset, but keeps this one: the user stays logged in.
 - Role and lock state are not on this page, and saving it never changes them, even if an admin changes them in
   the meantime.
 - Empty name or surname fields clear them.

### Collection CSV import and export

Status: built: "Export CSV" (`/account/export`) and "Import" (`/account/import`), in a "Your collection as CSV"
card under the account form.

Each user exports and imports only their own collection.

Decisions:

 - Columns: `title`, `authors`, `isbn`, `publisher`, `year`, `series`, `volume`, `plot`, `tags`, `copies`,
   `read`. Authors are "Surname, Name" (or "Surname" alone) separated by "; "; tags are separated by ", ", as in
   the book form; read is `yes` or `no`. The export is UTF-8 with a byte order mark (so spreadsheets read it as
   UTF-8), ordered by title.
 - An import needs a header with `title`; the other columns are optional and in any order (missing ones are
   empty, copies 1 and not read). An unknown or repeated column, an empty file, a file that is not UTF-8 or not
   CSV, or one with more than 1000 books refuses the whole file. Blank rows are skipped.
 - Each row matches a catalogue book: by ISBN when it has one (ISBN-10 converted); without one, by title and
   authors, ignoring case and the authors' order (the oldest such book). A row matching no book adds it to the
   catalogue, with the same rules as the add-book form.
 - Import and export are in the account area, not on the collection page: they are occasional tasks on the
   whole collection, and the collection page stays the list.
 - A book already in the collection is skipped, its copies and read left as they are; a book repeated in the
   file is added once.
 - Rows are imported one by one: a bad row (no title, invalid ISBN, year, copies or read, an author without a
   surname, a value too long) is reported with its row number as in a spreadsheet (the header is row 1), and
   the others are still imported. The card shows the count added (and new to the catalogue) and skipped, and
   the rows not imported; the collection itself is on its own page.
 - A cell starting with `=`, `+`, `-`, `@`, a tab or a carriage return is exported with a `'` before it, so a
   spreadsheet shows it as text instead of running it as a formula (catalogue texts come from every user); the
   import removes that `'` again.
 - With htmx the import replaces only the card. Without JavaScript it shows the account page with its report directly instead of redirecting:
   the report is too long for a flashed message, and posting the same file again only skips its books.
 - Request bodies are limited to 1 MiB (`MAX_CONTENT_LENGTH`); a larger upload answers 413.

Known limitations:

 - Matching without an ISBN is exact on title and authors: a different spelling adds another catalogue book.
 - The import does not update catalogue fields of books it matches.

## Books

Status: tables created by `init-db` (`BOOKS`, `AUTHORS`, `BOOK_AUTHORS`, `BOOK_TAGS`, `COLLECTION_BOOKS`). Books
can be listed (see Main page, and "One author's books"), added (see "Adding a book"), edited (every field but
the title, see "Editing a book"), kept in a collection (see "Your collection") and deleted by admins (see
"Deleting a book"). Authors are
listed and renamed on their own page (see "Authors").

Decisions:

 - The library is shared: there is one catalogue of books, and every user sees all of it.
 - Each user also has their own collection: the books from the shared catalogue that are theirs. A book a user
   adds goes into their collection automatically.
 - A user can add any book already in the catalogue to their own collection, and remove any book from it;
   removing it leaves the book in the catalogue. Users change only their own collection. Every user can see who
   owns each book and how many copies.
 - Every user, regular or admin, may add a book to the catalogue and edit any book in it, not only the books
   they added; which fields can be edited is in "Editing a book". Only admins may delete a book.
 - A book has a title (required) and optionally authors, ISBN, publisher, publication year, series, volume and
   plot (a summary of the story).
   A book can have several authors, in the order given; each author is a name and a surname.
 - Authors are their own records, shared between books: when a book is added or edited, each author is picked
   from the existing ones by name and surname, or created if new. All books by one author can be listed. A
   misspelled name is fixed once for every book by renaming the author (see "Authors"), or on one book by editing
   it (see "Editing a book").
 - An author is a name and a surname, and that pair is unique: two different authors cannot share both, so
   picking an author is never ambiguous. Authors may share a surname (Charlotte and Emily Brontë). The name (the
   given name) may be empty, for authors known by one name ("Homer"); it is stored as '' rather than NULL so that
   uniqueness still holds. An author used by a book cannot be deleted. An author no book uses any more after a book
   is edited is deleted, and so is an author merged into another by a rename; there is no other way to delete
   one.
 - Books are classified by free tags and a series: a book can have any number of tags, typed freely, and belong
   to one series. There are no genres or shelf locations. Tags are stored as text on each book, with no list of
   tags of their own.
 - The series is free text on each book ("Discworld"), ignoring case like the publisher, with no list of series
   of its own. The volume is the book's place in it, free text too ("2", "II", "3/4"), since volumes are numbered
   in many ways. Books have no language.
 - A book is a title: one record in the catalogue however many copies exist. The number of copies belongs to a
   user's collection: each user records how many copies of the book they own (at least one), so two users
   owning the same book is still one catalogue record.
 - Loans (who borrowed a book and when it is due back) are not tracked in the first version.
 - Each user can mark a book in their collection as read (see "Your collection"); a book is not read until they
   do. Other reading statuses (to read, reading) and ratings are not tracked in the first version.
 - Books are entered by hand in the first version. Filling in a book's details from an online service by ISBN
   (e.g. Open Library), and scanning the barcode with the phone camera, may come later.
 - Books have no cover images in the first version.
 - A book whose ISBN is already in the catalogue is refused. Any number of books may have no ISBN, and books
   with the same title and authors are allowed.
 - A book can be deleted only while it is in no user's collection; otherwise deletion is refused. Deleting a
   book removes it for good (it cannot be restored) and asks for confirmation first (see "Deleting a book").
 - The library can be exported as a CSV file. There is no import in the first version.
 - ISBNs are stored as 13 digits: hyphens and spaces are removed and an ISBN-10 is converted to its ISBN-13, so
   the same book typed either way is caught as a duplicate.
 - Titles, publishers, author names and surnames, and tags ignore case when compared and sorted ("Sci-fi" and
   "sci-fi" are one tag), but not accents ("Citta" and "Città" differ). All are trimmed; titles, surnames and
   tags cannot be blank. Empty optional fields (publisher, series, volume, plot, …) are stored as NULL.
 - Deleting a user removes their collection, which may leave books in no collection (and so deletable).
 - Sizes: title and publisher up to 255 characters, author name and surname up to 127 each, volume up to 32,
   series up to 255, tag up to 64, plot up to 4000. Name and surname are limited to 127 because the unique index on both must
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
 - Publisher and year share a row, and so do series and volume; the year and volume fields are narrow.
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

### Editing a book

Status: built at `/books/<id>`, opened from the titles in the main page and in "Your collection".

Clicking a book's title, in the main page or in "Your collection", opens the book in a modal dialog over the
list. Every field but the title can be changed; "Save" applies the changes, closes the dialog and
reloads the list with a confirmation. Without JavaScript the title is a link to the same form as a page
(`/books/<id>`).

Decisions:

 - Editable: authors, publisher, publication year, series, volume, plot and tags, with the same fields, rules and
   suggestions as when adding a book. Each may be emptied: an empty publisher, year, series, volume or plot is
   stored as NULL, no authors or no tags leave the book with none.
 - Authors are picked again on every save, as when adding: each row is the existing author with that name and
   surname (ignoring case), or a new one. So a misspelled author is fixed on a book by typing the right name
   (or picking it from the suggestions). An author that no book uses any more after a save is deleted, so the
   misspelled name stops being suggested.
 - The ISBN can be set only while the book has none. Once a book has an ISBN, regular users see it shown, not
   editable. A new ISBN follows the rules for adding a book: a valid ISBN-10 or ISBN-13, saved as ISBN-13, refused
   (with a message naming it) if another book in the catalogue has it.
 - Admins can correct a wrong ISBN: for them the ISBN stays a field, filled in, which they can change (same rules)
   or empty to remove the ISBN. A wrong but valid ISBN would otherwise stay forever, and also keep the real book
   with that ISBN out of the catalogue. The log records the old and the new ISBN.
 - The title is never editable: it is shown, not a field.
 - Any user may edit any book, as for adding; copies are not part of this form (they belong to "Your
   collection").
 - Tags are typed as for adding a book: separated by commas, blank ones skipped, a repeat (ignoring case) kept
   once. Saving replaces the book's tags with the ones typed.
 - Saving changes nothing else, so a save with no changes is harmless. The log records who edited which book and
   which fields changed.
 - If two users edit the same book at once, the last save wins for every field but a new ISBN, including an ISBN
   corrected by admins. When a book had no ISBN, a second save setting one is refused if the first one already
   set it ("This book already has an ISBN").

Technical details:

 - `find_book_details` loads every field (`BookDetails`); `update_book` saves in one read-committed transaction,
   like `add_book`. The ISBN is set with `UPDATE BOOKS SET ISBN = ? WHERE ID = ? AND ISBN IS NULL`: no row
   updated means the book already has one (`IsbnAlreadySetError`), so a regular user never changes an ISBN, even
   from a form opened before someone else set it. An admin's correction passes `replace_isbn=True`, which sets the
   ISBN, or NULL, without that condition; an ISBN saved unchanged is not written again, so a save of other fields
   does not undo a correction made meanwhile. The title is in no `UPDATE`, so it cannot change.
 - `EditBookForm` holds every editable field (authors, ISBN, publisher, year, series, volume, tags, plot) with their
   validators and the author-row handling; `BookForm`, for adding a book, adds the title and copies. The author
   rows are `_author_rows.html`, shared by both forms with an `id_prefix` (`book-`, `edit-book-`), since both
   dialogs are on the main page.
 - `update_book` replaces the book's `BOOK_AUTHORS` rows (deleted, then inserted in the new order), then deletes
   each former author with no book left; a foreign-key violation there means a concurrent transaction is linking
   that author, and it is kept.
 - The ISBN field is in the form while the book has none, and always for admins; an ISBN posted by a regular user
   for a book that has one is ignored.
 - The title link carries the list it was opened from (`list=collection`, or the catalogue by default) and its
   search, order and page; the form keeps them in hidden fields, and saving goes back there. Any other `list` value
   goes back to the catalogue, so the form cannot redirect elsewhere.
 - Templates: `book.html` (the page without JavaScript), `_book_details.html` (the dialog's content: the fields
   shown, then the form), `_edit_book_form.html` (the form, swapped alone when it has errors). The dialog is
   `#book-dialog` in `main.html` and `collection.html`.

Known limitations:

 - Changing only the case of an author's name ("frank herbert" to "Frank Herbert") changes nothing: the existing
   author is picked, as spelled. Renaming the author (see "Authors") changes it for every book.
 - An author left without books is kept if a book being added at that same moment already links to it. If
   instead the save deletes the author between the moment adding a book looks it up and the moment it links it,
   adding that book fails with the generic error and has to be submitted again. Both need two people working on
   the same author within milliseconds.

### Deleting a book

Status: built (`POST /books/<id>/delete`), from the "Delete book" button in the book dialog, for admins.

Decisions:

 - Only admins delete books. Regular users have no button, and a request from one is refused (403).
 - Only a book in nobody's collection can be deleted. For such a book the dialog says so, with how many
   collections hold it ("now 2"), instead of showing the button; the book's owners remove it from their
   collections first. A book added to a collection after the dialog was opened is refused when deleting, with
   that message in the error banner: the database's foreign key (`FK_COLLECTION_BOOKS_BOOK`) decides, so no
   book in a collection is ever lost.
 - Deleting asks for confirmation ("Delete “Dune”? This cannot be undone.") and removes the book for good, with
   its author links and tags. An author left without books is deleted too, as when editing a book.
 - Afterwards the list the book was deleted from comes back as it was, with a confirmation and the deletion
   logged with the admin who made it. When the list was one author's books and that author went with the book,
   it comes back as the whole list instead of an author page that no longer exists.

Technical details:

 - `delete_book` runs in its own read-committed transaction, as `update_book`, and returns the title and the
   authors it deleted (`DeletedBook`): the request's own snapshot transaction, started before the delete, would
   still see them.
 - The list's hidden fields (which list, author, search, order, page) are `_book_list_state.html`, shared by the
   edit and delete forms.

Known limitations:

 - As for deleting a user, the confirmation comes from htmx, so without JavaScript a book is deleted without
   asking. htmx is vendored with the app, so in practice it is always loaded.

## Main page

Status: built at `/`, with the add-book button (see Books, "Adding a book"); linked as "Catalogue" from the account
menu.

Decisions:

 - The main page is the book list, with a search box. There is no separate dashboard page.
 - One search box searches title, author and ISBN together. There are no separate filters (by author or tag).
 - The list is sorted by title by default; the user can sort it by author or by date added instead.
 - The list is shown in pages of 50 books.
 - On the phone, books are shown as a list of rows, not a grid (books have no covers; see Books).
 - Each row shows the title, the authors ("Name Surname", in their order) and the publication year. Each author
   links to their books (see "One author's books"); in "Your collection", to their books in the collection.
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
 - The search condition lives in one place, the `BOOK_MATCHES` PSQL function (`bookshelf/db/schema.py`, created
   or replaced by `init_db`), called by every list and count query of the catalogue and the collection.
 - Each list query also counts the books matching the search (`COUNT(*) OVER ()`), so a page is one scan. The
   separate count query runs only for a page past the last, to find the last one.

Known limitations:

 - The search looks for the text anywhere in titles, ISBNs and author names (`CONTAINING`), which no index can
   serve: every page reads every book (in the collection, every book of the user). That is fine for a personal or
   family library; tens of thousands of books would need another kind of search.

## Your collection

Status: built at `/collection`, linked from the account menu; the first page, shown after login and by the
"Bookshelf" title.

The books the user owns, from the shared catalogue: the same list as the main page (search, order, pages of 50),
showing only their books. Each row has the number of copies, editable, with "Save", a "Read" checkbox and a
"Remove" button.
It can be limited to one author's books (`/collection?author=<id>`, see "One author's books").

Decisions:

 - The collection has its own page; the main page stays the whole catalogue.
 - The collection is the first page: logging in, finishing the first-run wizard, the top bar's title and the
   error page's "Back to Bookshelf" lead to it, since a user mostly works with their own books. The catalogue
   keeps `/`; adding a book is done there (the collection has no add button).
 - Books are added to the collection from the main page's rows, or from the add-book form when the ISBN typed is
   already in the catalogue (see "Adding a book"). Adding a book already in the collection changes nothing and
   says so.
 - Removing a book takes one click, with no confirmation: it only leaves the collection, and can be added again
   from the main page (with one copy).
 - Changing the copies of, or removing, a book that is not in the collection (e.g. removed in another tab) shows
   an error saying so.
 - Adding a book that was deleted from the catalogue after the page was loaded answers 404, as for an unknown
   book.
 - Copies are between 1 and 999, as when adding a book.
 - "Read" is the user's own: each user marks their copy (`COLLECTION_BOOKS.IS_READ`), and a book starts as not
   read, also when it is added to a collection. With htmx, ticking or unticking the checkbox saves it at once and
   the list reloads with "“Dune” marked as read." (or "not read"). Without JavaScript the row has a Save button
   for it, inside `<noscript>` so it is never shown otherwise. The catalogue shows no read status.
 - With htmx, the rows' buttons (add, save, remove) and the read checkbox reload only the list, keeping its search, order and page,
   with a message above it. Without JavaScript they are plain forms, and the server redirects back to the list
   with the same search, order and page and the message shown on it.
 - "Date added" orders the collection by when each book entered the catalogue: the collection does not record
   when a book joined it.

Known limitations:

 - Saving copies or removing a book reloads the list, so the focus goes back to the top of the page.

## Authors

Status: built at `/authors`, linked from the account menu.

Every author in the catalogue, by surname then name ("Herbert, Frank"; "Homer" alone), with how many books are
theirs, a search box (name or surname, ignoring case) and pages of 50. An author's name opens their books; each
row's "Rename" opens a dialog to change the author's name and surname, for every book at once.

Decisions:

 - Any user may rename an author, as any user may edit any book. The rename is logged with who did it.
 - Same rules as an author row: the surname is required, the name optional (empty for authors known by one
   name), both trimmed, at most 127 characters.
 - Changing only the case ("frank herbert" to "Frank Herbert") renames the author in place, which editing a book
   cannot do.
 - Renaming to a name another author already has (ignoring case) merges the two, since a name and surname pair
   is unique: every book of the renamed author gets the other one, as that one is spelled, and the renamed author
   is deleted. A book that had both keeps one, in the other author's place. The confirmation says "merged into"
   instead of "renamed to". There is no separate merge action, and no confirmation step: the dialog says that a
   name already in use joins the two.
 - After renaming, the list reloads with a confirmation, keeping its search and page. Without JavaScript the
   Rename link opens a page (`/authors/<id>`) with the same form.

Technical details:

 - `bookshelf/models/authors.py`: `list_authors`, `find_author` and `rename_author`, which runs in its own
   read-committed transaction (as `add_book`), so an author with the new name committed by someone else meanwhile
   is seen: the rename then becomes a merge into that author.
 - A merge first deletes the renamed author's links to books that already have the other author, then points the
   remaining links at the other author, then deletes the renamed author. If a book being added at that moment
   links the renamed author, it is kept with that book. After a merge a book's author order can skip a number
   (1, 3), which only the order matters for.

Known limitations:

 - A merge cannot be undone: the renamed author's spelling is gone, and splitting the books apart again means
   editing them one by one.

### One author's books

Status: built at `/?author=<id>` (the catalogue) and `/collection?author=<id>` (the user's own books by that
author). The first is linked from each name on the authors page and from the authors under each book in the
catalogue; the second from the authors under each book in the collection.

The catalogue limited to one author's books, headed "Books by Frank Herbert", with links to all books and to the
authors page. In the collection, "Your books by Frank Herbert", with links to all your books and to all books by
that author.

Decisions:

 - It is the main page's (or the collection's) list, not a page of its own: search, order, pages, the row buttons
   ("Add to collection"; copies and "Remove") and the edit dialog all work as in the whole list, and keep to the
   author. The list's links and forms carry the author
   along with its search, order and page, and saving a book in the dialog goes back to it.
 - The search searches only the author's books (title, any of their authors, ISBN).
 - An unknown or malformed author ID is a 404, as is an author merged away since the link was made.
 - An author under a book links to their books in the list it is shown in, so browsing the collection stays in
   the collection.

Technical details:

 - The filter is the `AUTHOR_FILTER` argument of `BOOK_MATCHES` (NULL for every book), so the list and count
   queries are unchanged; `list_books` and `list_collection` take `author_id`, and `author_filter()` in
   `routes/main.py` reads it from a list's query string or form.
 - The book list queries also read each author's ID, for the links: `Author` has an optional `id`, which plays
   no part in comparing authors (still by name and surname).

