# Sessions des utilisateurs
last-checked: 2026-11-18

Cette page permet de voir où un compte est connecté et d'en terminer les sessions. Elle s'adresse au support et au propriétaire de la plateforme.

## Étapes
1. Saisissez l'e-mail du compte dans la recherche et choisissez Rechercher le compte. L'e-mail est envoyé dans le corps de la requête, pas dans l'adresse.
2. Le tableau Sessions actives montre Appareil, Dernière utilisation et Réseau de chaque session.
3. Sous Terminer les sessions de cet utilisateur, indiquez un motif et choisissez Terminer les sessions de cet utilisateur.

## Garde-fous
Mettre fin aux sessions demande à chaque fois une passkey récente et un motif, et est inscrit dans le journal d'audit.

## Bon à savoir
Le compte est déconnecté partout. Les clés API et les liens envoyés par e-mail, qui ne sont pas des sessions, ne sont pas révoqués. Pour déconnecter tout le monde, le propriétaire utilise Terminer toutes les sessions.
