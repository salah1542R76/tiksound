# Passage de Tiksound à TikWave : ce qu'il reste à faire en ligne

Le code est prêt et testé, mais il n'est pas encore envoyé sur GitHub. Chaque étape ci-dessous se fait avec ton accord, et c'est toi qui saisis les clés et les mots de passe.

## Déjà vérifié (26/11/2026, en lecture seule)

- https://tikwave.fr répond : c'est bien le même serveur que l'ancienne adresse.
- Le certificat est valide jusqu'au 24/02/2027, et Render le renouvelle tout seul.
- https://www.tikwave.fr redirige vers https://tikwave.fr.
- L'ancienne adresse https://tiksound-cqj3.onrender.com marche toujours.
- tikwav.fr (sans E) n'appartient à personne : ne pas l'utiliser.

## 1. Avant l'envoi sur GitHub : vérifier `MAIL_FROM` sur Render

Render → service « tiksound » → Environment → regarde la valeur de `MAIL_FROM`.

- **Si elle existe** (par exemple `Tiksound <adresse@…>`), remplace seulement le nom : `TikWave <adresse@…>`. Garde la même adresse tant que tikwave.fr n'est pas vérifié chez le service d'e-mail.
- **Si elle n'existe pas**, le nouveau code enverra depuis `no-reply@tikwave.fr`. Les codes d'inscription ne partiront plus tant que ce domaine n'est pas vérifié (étape 4). Dans ce cas, crée `MAIL_FROM` avec l'adresse utilisée aujourd'hui.

Ne touche pas aux domaines d'e-mail de ton autre projet chez Resend.

## 2. Render : variable `PUBLIC_URL`

Mets `PUBLIC_URL` = `https://tikwave.fr`. Elle sert à l'adresse de retour après un paiement et aux aperçus de liens partagés. Toutes les autres variables restent identiques, y compris `R2_BUCKET=tiksound`.

## 3. GitHub : envoi du code

Render redéploie automatiquement après l'envoi (quelques minutes).

## 4. E-mails depuis tikwave.fr (facultatif, plus tard)

Ajoute tikwave.fr chez Resend ou Brevo. Le service donne quelques lignes DNS à ajouter chez Gandi. Resend les place sur des sous-domaines (`send.`, `resend._domainkey`), donc les lignes d'e-mail déjà présentes chez Gandi ne bougent pas. Une fois le domaine vérifié : `MAIL_FROM` = `TikWave <no-reply@tikwave.fr>`.

## 5. Stripe (environnement « environnement de test tiksound » uniquement)

1. Développeurs → Webhooks → **tiksound** → Modifier l'adresse. Remplace l'ancienne par `https://tikwave.fr/api/stripe/webhook`. Garde les 5 événements et le format.
   - Modifier l'adresse ne change pas la clé de signature (`whsec_…`). Vérifie-le en la révélant : elle doit commencer pareil que `STRIPE_WEBHOOK_SECRET` sur Render.
2. Catalogue → **Tiksound Premium** → renommer en **TikWave Premium**. C'est le nom affiché sur le paiement et les factures, et ce produit ne concerne que TikWave. L'identifiant du tarif ne change pas, donc les abonnements en cours continuent.
3. **Ne pas toucher** : les webhooks de ton autre projet, le produit « token » (le code TikWave ne l'utilise pas), l'environnement « Mode test ».
4. **Nom public et marque du compte** : ils sont partagés avec ton autre projet et apparaîtraient sur les paiements des deux. À ne changer qu'après ta décision. Changer seulement le nom du produit (étape 2) suffit pour TikWave.

## Autres services utilisés par le code

| Service | Ce qui change |
|---|---|
| Neon (base de données, `DATABASE_URL`) | Rien |
| Cloudflare R2 (sons en ligne, bucket `tiksound`) | Rien : le serveur autorise la lecture depuis n'importe quelle adresse |
| Google AdSense (pubs des comptes gratuits) | Ajoute tikwave.fr dans AdSense → Sites. Le fichier `/ads.txt` est servi automatiquement sur la nouvelle adresse |
| Connexion des comptes | Faite par le site lui-même (aucun service externe, aucune adresse de retour à changer) : il faut juste se reconnecter sur tikwave.fr |
| Spotify (recherche), YouTube, SoundCloud, TikTok | Rien |
| Lien de don PayPal | Rien |

## 6. Ce que vivent les utilisateurs

- Le navigateur range les données par adresse. Sur tikwave.fr, il faut **se reconnecter**.
- Avec un compte, les titres et les playlists reviennent tout seuls (synchronisation) ; les sons gardés hors ligne sont à retélécharger.
- Sans compte, la bibliothèque reste sur l'ancienne adresse. Là-bas, un bandeau explique comment exporter une sauvegarde et l'importer sur tikwave.fr. Les sauvegardes « tiksound » sont acceptées.
- Les abonnements, les crédits et les clés d'accès déjà données (`TKS-…`) sont gardés : rien ne change dans la base de données.
- Ne redirige pas l'ancienne adresse vers tikwave.fr avant que les gens aient eu le temps d'emporter leurs sons, au moins quelques semaines. Si tu rediriges un jour, il faudra laisser passer `/api/stripe/webhook`.

## 7. Essais après la mise en ligne

1. Ouvre https://tikwave.fr, choisis English dans Profil → Langue, puis reviens en Français.
2. Connecte-toi.
3. Fais un paiement de test avec la carte de test Stripe `4242 4242 4242 4242` (date future, n'importe quel code).
4. Stripe → Webhooks → tiksound → Événements : les envois doivent répondre **200**.
5. Publie un son, puis ouvre Explorer : il doit apparaître en haut des « Sons publiés ».

## Risques à connaître

- **Nom « TikWave »** : « Tik » rappelle fortement TikTok (marque de ByteDance), surtout pour un site qui récupère des sons TikTok. Vérifie sur INPI (data.inpi.fr) et EUIPO (euipo.europa.eu, TMview) si « TikWave » ou un nom proche est déjà déposé, et demande l'avis d'un juriste avant de faire de la publicité.
- **Statut Discord** : voir `discord/README.md`. PreMiD n'accepte pas les domaines de moins de 2 mois, et peut refuser un site de téléchargement.
