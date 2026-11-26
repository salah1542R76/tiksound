# TikWave Premium : design (approuvé le 2026-11-24)

## Intention
Monétiser TikWave par des pubs et un abonnement mensuel à 2,99 €. Le propriétaire (admin) ne paie pas et peut offrir le Premium à ses amis avec des clés d'accès.

## Objectifs
- Gratuit : sons existants (stockés sur l'appareil) toujours lisibles, playlists intactes. Une pub tous les 5 sons + bandeaux pub dans l'appli. Plus de mode hors ligne : les nouveaux sons sont stockés en ligne (R2) et lus en streaming.
- Premium : aucune pub (ni interstitielle ni bandeau), hors ligne autorisé, badge sur le profil.
- Statut Premium décidé côté serveur (Stripe, clé d'accès ou admin). Le navigateur ne fait que mettre en cache l'échéance pour l'usage hors ligne.
- Clés d'accès gérées par l'admin dans le profil.

## Non-objectifs
- Pas de réseau publicitaire (AdSense etc.), pas de plan annuel, pas de remboursement dans l'appli, pas de DRM sur les fichiers R2 (URL publiques : on bloque dans l'appli et sur le serveur, pas le « clic droit enregistrer »).

## Anti-objectifs (échec même si ça « marche »)
- Un utilisateur actuel qui perd l'accès à ses sons ou à ses playlists.
- Un Premium payé qui saute à cause d'un webhook en retard ou dans le désordre.
- Une résiliation Stripe qui retire un Premium offert par clé.
- Une clé ou un secret Stripe dans le code.

## Contraintes
- Backend Flask + SQLAlchemy (SQLite en local, PostgreSQL en prod), frontend `index.html` unique, français.
- Gunicorn 2 workers : l'idempotence et les compteurs de clés passent par la base, pas par la mémoire.
- Variables d'environnement : `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID`, `STRIPE_WEBHOOK_SECRET`, `ADMIN_EMAIL` (+ optionnelle `PUBLIC_URL`).

## Approche retenue
Le serveur est la source de vérité ; le webhook Stripe sert de déclencheur : à chaque événement on relit l'abonnement chez Stripe et on applique son état (actif / pas actif), ce qui rend le traitement indépendant de l'ordre des événements. Alternative écartée : appliquer le contenu de chaque événement tel quel (fragile si les événements arrivent dans le désordre ou selon la version d'API Stripe).

## Règles
- `PAYWALL_ON` = `STRIPE_SECRET_KEY` et `STRIPE_PRICE_ID` définis **et** R2 configuré. Sinon l'appli se comporte comme avant (pas de pub, hors ligne autorisé).
- Premium actif si : admin (email dans `ADMIN_EMAIL`) **ou** `gift_until` > maintenant **ou** `premium_until` + 48 h de grâce > maintenant.
- Webhook (signature vérifiée, idempotent via table `stripe_events` dans la même transaction) : `checkout.session.completed`, `invoice.paid`, `invoice.payment_failed`, `customer.subscription.deleted` (+ `customer.subscription.updated`). Abonnement `active`/`trialing` → `premium_until` = fin de période ; sinon → 0.
- `/api/extract` : si `PAYWALL_ON` et non Premium → stockage en ligne forcé ; si R2 échoue, erreur (pas de repli en local).
- Suppression de compte : résiliation de l'abonnement Stripe d'abord.
- Clés : `TKS-XXXX-XXXX-XXXX`, stockées hachées (HMAC), affichées une seule fois à la création, nombre d'utilisations (0 = illimité) et durée (vide = à vie), révocables (retire le Premium de ceux qui l'ont utilisée), tentatives limitées par IP.
- Pubs : une seule liste `ADS` dans `index.html` ; `ADS_EVERY = 5` ; audio / image / texte ; la liste vide = pas de pub ; jamais hors ligne.

## Laissé au constructeur
Styles, textes exacts, emplacement précis des bandeaux (Accueil, Mes sons, Playlists, Explorer), détails de l'API admin.
