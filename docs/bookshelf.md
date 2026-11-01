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
 - Email addresses are case-insensitive (stored lowercase).
 - Wrong password, unknown email and locked account all show the same error, "Invalid email or password", so the
   page does not reveal which accounts exist.
 - Locking a user ends their open sessions on the next request.

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

### Open questions

 - Is there a limit on failed login attempts?

## Main page

This is the main page