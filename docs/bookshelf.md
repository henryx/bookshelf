# Bookshelf

This is the bookshelf application.

# Features

## General

Frontend is developed using HTMX library as PWA, to permit to install on mobile devices.

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
   reveal which accounts exist. Refused attempts are not counted, and a successful login clears that email's
   failures from that IP. Failures are stored in the `LOGIN_FAILURES` table with the client IP (see
   `APP_TRUSTED_PROXIES` behind a reverse proxy, or every client shares the proxy's IP). The CLI is not limited.
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

## Main page

This is the main page