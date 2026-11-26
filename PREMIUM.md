# TikWave Premium : guide de configuration

Le guide complet, illustré et à cocher, est dans la page **`guide-premium.html`** (ouvre-la dans ton navigateur, ou utilise le lien de la page publiée dans la conversation).

Aide-mémoire, au cas où tu n'as que ce fichier :

| Variable Render | Rôle |
|---|---|
| `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_PRICE_ID`, `STRIPE_WEBHOOK_SECRET` | Abonnement 2,99 € par mois, formulaire de paiement intégré (obligatoires) |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_PUBLIC_URL` | Stockage en ligne (obligatoire : sans lui l'abonnement reste désactivé) |
| `ADMIN_EMAIL` | Ton compte : Premium à vie, peut créer des clés d'accès |
| `PUBLIC_URL` | Facultatif : adresse du site sans « / » final |
| `ADSENSE_CLIENT`, `ADSENSE_SLOT` | Pubs Google AdSense (bandeau fixe, comptes gratuits seulement) |
| `CONTACT_EMAIL` | Adresse affichée sur `/confidentialite` |

Webhook Stripe : `https://tikwave.fr/api/stripe/webhook`, 5 événements : `checkout.session.completed`, `invoice.paid`, `invoice.payment_failed`, `customer.subscription.updated`, `customer.subscription.deleted`.

Vérification : `https://ton-site/api/health` doit afficher `"paywall": true`.

Attention : Google AdSense risque de refuser un site qui convertit des sons TikTok en MP3 (règle sur les droits d'auteur). Le détail est dans le guide.

Facultatif, pour que l'égaliseur et la réverbe marchent aussi sur les sons en ligne : Cloudflare → R2 → bucket `tiksound` → Settings → CORS Policy → ajoute :
`[{"AllowedOrigins":["https://ton-site"],"AllowedMethods":["GET","HEAD"],"AllowedHeaders":["*"],"MaxAgeSeconds":86400}]`
Sans ça, tout marche quand même : les sons en ligne se lisent simplement sans effet.

Réservé à Premium (en plus de « sans pub » et du hors ligne) : presets d'égaliseur Basses max / Aigus / Chaleureux / Lo-fi / Nuit / Perso, réverbe, « Slowed + reverb », thèmes Bordeaux / Pastel / Synthwave, fonds Vagues / Étoiles / Dégradé animé, polices Playfair / Caveat / Pixel, dégradé de couleurs, couleur de la pochette.

Tests (chaque fichier séparément) : `python3 -m pytest tests -q` (serveur), `python3 -m pytest tests/e2e_front.py -q` et `python3 -m pytest tests/e2e_v2.py -q` (appli dans Chrome).
