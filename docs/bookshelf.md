# Bookshelf

This is the bookshelf application  

# Features

## General

Frontend is developed using HTMX library as PWA, to permit to install on mobile devices.

## Authentication

### Technical details

Data for login are email and password. They are stored in a database table called USERS. Password is hashed with Argon2id
using a random per-password salt (e.g. `argon2-cffi` `PasswordHasher`); the salt and parameters are stored inside the
hash string, so no separate salt column is needed. Users can be two roles: regular and admin. Admin role permit to user
to made admin operations such add or delete new users.

### Login page

When user open for the first time the webapp, a login page is shown. If login and password are correct, main page is
shown, otherwise login page show an error. A persistent cookie is saved to permit user to navigate in the webapp without
repeat login. If cookie is expired, or user made a logout action, next page showed is login page.

### Admin area

Admin area is accessible only to admin users. In this area, are shown all users stored in the system. An admin can be:
 - create and delete an user.
 - promote an user as administrator.
 - lock an user.
 - reset the password for the user.

### Open questions

 - How is the first admin created (CLI command, env-seeded user, setup page)?
 - Can an admin delete, demote, or lock themselves or the last remaining admin?
 - Password reset: admin sets a temporary password, or another mechanism (no email is available)?
 - What does a locked user see on the login page?
 - Is there a limit on failed login attempts?

## Main page

This is the main page