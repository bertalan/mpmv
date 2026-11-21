# Paiements orphelins
last-checked: 2026-11-21

Cette page vous montre les paiements réussis mais non utilisés pour une commande, et vous permet de fermer leurs alertes une fois réglées. Le Support et le propriétaire de la plateforme ferment les alertes ; la Finance peut lire la liste.

## La liste
Chaque ligne indique la référence du paiement, la référence de la commande, pourquoi le paiement est orphelin (la commande n'attend plus de paiement, le montant ou la devise ne correspondent pas, la référence du paiement est erronée, ou aucune commande ne correspond), le montant, l'âge en jours et si le prestataire de paiement confirme un remboursement complet.

## L'état du remboursement
La page n'interroge pas le prestataire de paiement. Elle affiche la dernière vérification : la tâche de nuit vérifie chaque alerte à partir du 14e jour, et Pas encore vérifié signifie qu'aucune vérification n'est enregistrée. Pour vérifier une alerte tout de suite, choisissez Vérifier maintenant dans sa ligne : le prestataire est interrogé une fois et le résultat est enregistré avec son heure.

## Fermer une alerte
1. Remboursez le paiement à la main chez le prestataire de paiement.
2. Dans sa ligne, saisissez le motif de la fermeture (par exemple, comment il a été remboursé) et choisissez Fermer l'alerte.

L'alerte quitte la liste et le rappel quotidien s'arrête. Le journal d'audit ne garde qu'une empreinte du motif : n'y écrivez donc pas de données personnelles. Une alerte déjà fermée reste fermée.

## Bon à savoir
Une alerte ouverte depuis 14 jours est fermée par la tâche de nuit dès que le prestataire confirme un remboursement complet ; jusque-là, le Support reçoit un rappel par jour.
