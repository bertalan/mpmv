# Stray payments
last-checked: 2026-11-21

Use this page to see the payments that succeeded but were not used for an order, and to close their alerts once they are settled. Support and the platform owner close alerts; Finance can read the list.

## The list
Each row shows the payment reference, the order reference, why the payment is stray (the order no longer awaits payment, the amount or currency does not match, the payment reference is wrong, or no order matches), the amount, the age in days and whether the payment provider confirms a full refund.

## The refund state
The page does not ask the payment provider. It shows the last check: the nightly job checks each alert from day 14, and Not checked yet means no check is recorded. To check one alert now, choose Check now in its row: the provider is asked once and the result is recorded with its time.

## Close an alert
1. Refund the payment by hand at the payment provider.
2. In its row, write the reason for closing (for example, how it was refunded) and choose Close the alert.

The alert leaves the list and the daily reminder stops. Only a fingerprint of the reason is kept in the audit log, so do not write personal data in it. An alert that is already closed stays closed.

## Good to know
An open alert 14 days old is closed by the nightly job once the provider confirms a full refund; until then support is reminded once a day.
