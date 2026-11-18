# End every session
last-checked: 2026-11-18

This page signs out every user, staff and vendors included, everywhere except your current session. It is for the platform owner only and is meant for an emergency.

## What it does
- The page shows how many sessions are open now.
- API keys and links sent by email are not sessions and stay valid: revoke them separately.

## Safeguards
- Type the confirmation words exactly as shown, then choose End every session.
- It asks for a fresh passkey each time.
- The action is written to the audit log with the number of sessions, and the platform staff are told by e-mail.

## Good to know
Nothing is undone by this action: people must sign in again. For a single account, use User sessions instead.
