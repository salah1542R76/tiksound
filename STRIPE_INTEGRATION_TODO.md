# Stripe : formulaire de paiement intégré (Checkout Studio)

Ce fichier est la seule source pour ce qu'il reste à faire. Le code est en place ; il manque surtout des réglages chez Stripe et sur Render.

## Values to Replace

Aucune valeur factice (`price_...`) n'est écrite dans le code : `mode` est déjà `"subscription"` et le tarif vient de la variable d'environnement `STRIPE_PRICE_ID`. Ce qui reste à renseigner, ce sont les variables d'environnement sur Render (ou dans ton `.env` en local).

**Files containing the settings:**
- [server.py](server.py) (lit les variables au démarrage)

| Field | Current Value | What to Set |
|-------|--------------|-------------|
| `STRIPE_PUBLISHABLE_KEY` | *(vide)* | Ta clé publiable `pk_test_...` (Dashboard → Développeurs → Clés API : https://dashboard.stripe.com/test/apikeys). **Nouvelle variable, obligatoire** : sans elle l'abonnement reste désactivé. |
| `STRIPE_SECRET_KEY` | `sk_test_...` | Ta clé secrète, même page. Jamais dans le code. |
| `STRIPE_PRICE_ID` | `price_...` | L'identifiant du tarif récurrent 2,99 € / mois (https://dashboard.stripe.com/prices). |
| `STRIPE_WEBHOOK_SECRET` | `whsec_...` | La clé de signature de l'endpoint `https://TON-SITE/api/stripe/webhook` (https://dashboard.stripe.com/workbench/webhooks). |

Variables serveur uniquement : ce projet n'utilise pas Vite, donc aucun préfixe `VITE_`. La clé publiable est servie au navigateur par `/api/premium`, elle n'est écrite dans aucun fichier.

## Configured Parameters

Ces paramètres viennent de Checkout Studio et sont déjà réglés dans `stripe_create_checkout`.

**Files containing these parameters:**
- [server.py](server.py)

| Parameter | Value |
|-----------|-------|
| ui_mode | form |
| billing_address_collection | auto |
| phone_number_collection | {"enabled": false} |
| automatic_tax | {"enabled": false} |
| payment_method_collection | always |
| submit_type | auto |
| integration_identifier | custom_embedded_web_0001 |

Côté navigateur ([index.html](index.html)) : Stripe.js est chargé dans le `<head>` depuis `https://js.stripe.com/dahlia/stripe.js`, puis `Stripe(clé publique)`, `initCheckoutFormSdk({clientSecret, appearance})`, `createForm({layout: 'expanded'})`, `mount('#checkout-form')`, `loadActions()` et l'événement `confirm`, avec l'apparence de Checkout Studio.

## Écarts par rapport au texte de la tâche (à connaître)

1. **Paramètres conservés alors qu'ils ne figurent pas dans Checkout Studio** : `customer`, `client_reference_id` et `subscription_data.metadata.user_id`. Le webhook s'en sert pour savoir quel compte TikWave a payé. Les retirer ferait débiter des gens sans leur donner le Premium.
2. **`success_url` et `cancel_url` supprimés, `return_url` ajouté** : la documentation Stripe indique que `success_url` n'est pas permis avec les modes intégrés, et le guide du formulaire intégré utilise `return_url`. Après paiement, Stripe renvoie vers `/?premium=success` (même page qu'avant, déjà gérée). Il n'y a plus de retour « annulé » : on ferme simplement la fenêtre.
3. **L'abonnement exige maintenant `STRIPE_PUBLISHABLE_KEY`** (en plus de la clé secrète, du tarif et de R2), sinon les pubs s'afficheraient sans moyen de payer pour les retirer.
4. **Bibliothèque Stripe** : `requirements.txt` passe à `stripe>=15.0.0` (la version 15 utilise l'API `2026-03-25.dahlia`, requise par `ui_mode: "form"`). Python 3.9 ou plus est nécessaire. Dans cette version les objets Stripe ne sont plus des `dict` ; la fonction `_plain` accepte les deux.
5. Stripe affiche aujourd'hui une version d'API plus récente (`2026-11-18.endive`, script `js.stripe.com/endive`). Le texte de la tâche demande `dahlia` ou plus ; `dahlia` reste donc utilisé.

## Setup and next steps

**Mise en route**
1. Dans Stripe (mode test), copie la clé publiable `pk_test_...` et ajoute `STRIPE_PUBLISHABLE_KEY` sur Render. Les autres variables sont décrites dans [guide-premium.html](guide-premium.html).
2. Redéploie : Render installe `stripe>=15.0.0`. Vérifie `https://TON-SITE/api/health` : `"paywall": true`.
3. Ouvre l'étoile Premium, puis **Passer Premium** : le formulaire Stripe s'affiche dans la fenêtre.

**Comment ça marche**
1. Le navigateur appelle `POST /api/stripe/checkout` (compte connecté obligatoire).
2. Le serveur crée (ou retrouve) le client Stripe et une session Checkout en `ui_mode: "form"`, puis renvoie `{"client_secret": "..."}` en JSON.
3. Le navigateur monte le formulaire Stripe dans la fenêtre Premium. Au paiement, Stripe redirige vers `/?premium=success`.
4. Le webhook `checkout.session.completed` (puis `invoice.paid`, etc.) active le Premium côté serveur ; la page attend jusqu'à 30 secondes.

**Fichiers touchés** : [server.py](server.py), [index.html](index.html), [requirements.txt](requirements.txt), [tests/](tests/). Aucun nouveau fichier de code.

**Cartes de test** (mode test uniquement)

| Cas | Numéro |
|-----|--------|
| Paiement accepté | 4242 4242 4242 4242 |
| Validation 3D Secure | 4000 0025 0000 3155 |
| Paiement refusé | 4000 0000 0000 9995 |
| Carte enregistrée, mais débit refusé au renouvellement | 4000 0000 0000 0341 |

Date de validité dans le futur, CVC au choix.

**Ce qui n'a pas pu être vérifié ici** : les appels réels à Stripe (création de session, version 15 de la bibliothèque, vrai Stripe.js) sont remplacés par des doublures dans les tests. Fais un paiement de test complet avant de passer en mode réel.

**Pour la suite**
- Passer en mode réel : nouvelles clés (`pk_live_...`, `sk_live_...`), nouveau tarif et nouveau webhook en mode réel.
- Changer le prix : crée un nouveau tarif chez Stripe et mets son identifiant dans `STRIPE_PRICE_ID`.
- Les paiements apparaissent dans Dashboard → Paiements ; l'activation du Premium se fait uniquement par le webhook.

**Ressources** : https://support.stripe.com et https://docs.stripe.com/mcp
