# User sessions
last-checked: 2026-11-18

Use this page to see where one account is signed in and to end its sessions. It is for Support and the platform owner.

## Steps
1. Type the account's e-mail in the lookup and choose Find the account. The e-mail is sent in the request body, not in the address.
2. The table Signed-in sessions shows the Device, Last used and Network of each session.
3. Under End this user's sessions, give a reason and choose End this user's sessions.

## Safeguards
Ending sessions asks for a fresh passkey each time and a reason, and is written to the audit log.

## Good to know
This signs the account out everywhere. It does not revoke API keys or links sent by email, which are not sessions. To sign out everyone, the owner uses End every session.
