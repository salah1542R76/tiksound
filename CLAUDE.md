# TikWave (ex-Tiksound)

Site de musique (PWA) : on colle un lien TikTok (ou on cherche un son sur YouTube / SoundCloud, ou on importe une playlist Spotify), le serveur garde uniquement le son en MP3, et l'appli le range dans une bibliothèque avec playlists, effets audio, personnalisation et abonnement Premium.

Le propriétaire n'est pas développeur : il parle français, tutoie, et veut des explications simples sans jargon. Interface en français (au tutoiement) et en anglais.

**Nom : TikWave. Domaine : https://tikwave.fr** (acheté chez Gandi le 26/11/2026, DNS chez Gandi, branché sur Render, certificat valide). L'ancien nom était Tiksound, ancienne adresse https://tiksound-cqj3.onrender.com (toujours active, même serveur). Un premier brouillon disait « Tikwav / tikwav.fr » : ce domaine n'est enregistré par personne, ne pas l'utiliser.

## Fichiers

| Fichier | Rôle |
|---|---|
| `server.py` | Toute l'API Flask : comptes, extraction audio (yt-dlp + ffmpeg), recherche, Spotify, stockage R2, relais `/api/media`, Stripe, social, admin |
| `i18n_server.py` | Traductions anglaises du serveur (messages d'erreur, e-mails, aperçus de liens) et choix de la langue d'une requête |
| `index.html` | Toute l'appli front en un seul fichier (HTML + CSS + JS, ~380 Ko) |
| `lang-en.js` | Traductions anglaises de l'appli (chargé seulement pour les visiteurs en anglais) |
| `sw.js` | Service worker (cache `tk-vN` : augmenter N à chaque livraison d'`index.html` ou de `lang-en.js`, avec `?v=N` dans `index.html`, et mettre à jour `tests/test_icons.py`). Actuellement `tk-v12`. Le `SHELL` (page, manifest, `lang-en.js?v=N`, icônes) est gardé dès l'installation : l'appli s'ouvre hors ligne après une seule visite |
| `promo.mp4`, `promo-poster.jpg` | Vidéo de présentation de l'accueil (nouveaux visiteurs). Servie par `statique` (morceaux 206, cache long via `?v=N`), jamais gardée par le service worker. La remplacer = changer `?v=` dans `index.html` |
| `manifest.webmanifest`, icônes, favicons | PWA installable (le serveur traduit le manifest en anglais) |
| `conditions.html`, `confidentialite.html`, `signalement.html` + `*.en.html` | Pages légales en français et en anglais (à faire relire) |
| `guide-premium.html`, `PREMIUM.md` | Guide de configuration Stripe / R2 / AdSense |
| `discord/` | Statut Discord via l'extension PreMiD : module prêt, à envoyer à PreMiD (voir `discord/README.md`) |
| `MIGRATION-TIKWAVE.md` | Étapes en ligne du renommage (Render, Stripe, e-mails) et ce que vivent les utilisateurs |
| `Dockerfile`, `Procfile`, `requirements.txt` | Déploiement Render |
| `tests/` | Tests serveur (pytest) et tests de l'appli dans Chrome (Playwright) |
| `docs/superpowers/` | Spec et plan du Premium (historique) |

## Déploiement

- GitHub (`salah1542R76/tiksound`, branche `main`, dépôt PUBLIC : aucun secret dans le code) → Render redéploie automatiquement à chaque push. Base de données Neon (PostgreSQL) via `DATABASE_URL`, fichiers audio sur Cloudflare R2.
- Le `Dockerfile` installe ffmpeg et Deno (YouTube exige un moteur JavaScript pour yt-dlp depuis fin 2025). Gunicorn en 2 processus × 8 fils, délai 150 s.
- `requirements.txt` impose `yt-dlp[default]>=2026.11.23` : YouTube casse les vieilles versions en quelques semaines.

Variables Render : voir `PREMIUM.md` (Stripe, R2, `ADMIN_EMAIL`, `PUBLIC_URL`, AdSense, `CONTACT_EMAIL`). `PUBLIC_URL` doit valoir `https://tikwave.fr` (retour après paiement, aperçus de liens). Facultatives : `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`, `YTDLP_COOKIES` / `YTDLP_PROXY`, `R2_AUTO_CORS=0`, `YT_BLOCK_S`, e-mails (`RESEND_API_KEY` ou Brevo ou SMTP, `MAIL_FROM`). Webhook Stripe : `https://tikwave.fr/api/stripe/webhook`.

Le compte Stripe est partagé avec un autre projet du propriétaire : ne jamais toucher à un webhook, un produit ou un domaine d'e-mail qui n'est pas celui de TikWave (webhook « tiksound », produit Premium), ni au nom ou à la marque du compte sans son accord. Le produit « token » du compte n'est pas utilisé par ce code.

## Tests

Lancer chaque groupe séparément (les tests Chrome dans leur propre processus) :

```
python3 -m pytest tests -q --ignore=tests/e2e_front.py --ignore=tests/e2e_v2.py   # serveur (223)
python3 -m pytest tests/e2e_front.py -q                                           # appli (53)
python3 -m pytest tests/e2e_v2.py -q                                              # appli (248)
```

Dépendances : `pip install -r requirements.txt pytest playwright pillow opencv-python-headless numpy` puis `playwright install chromium` (ou `CHROME_BIN` vers un Chrome installé). Sous Windows : `PYTHONUTF8=1`. `tests/_shims.py` simule sqlalchemy et yt_dlp s'ils manquent : aucun test ne touche Internet. Les navigateurs de test sont en `fr-FR` par défaut (`tests/e2e_front.py`). Méthode : test qui échoue d'abord, puis correction, puis les trois groupes au vert avant de livrer.

## Fonctionnement à connaître

- **Audio** : deux lecteurs `<audio>`. `au1` est simple. `au2` passe par Web Audio (égaliseur, distorsion, réverbe, compresseur). `au` désigne celui en cours, `pickEl()` choisit, et `reroute()` change de lecteur en gardant la position et la vitesse.
- **Écran éteint** (`visibilitychange`) : la lecture repasse sur `au1`, car iOS et Android coupent Web Audio en arrière-plan.
- **Effets sur les sons en ligne** : si R2 refuse la lecture directe (CORS), le son passe par `GET /api/media/<clé>`. Les gardes `REROUTEN` / `LOADN` évitent que deux changements rapides se marchent dessus.
- **Recherche** : `GET /api/find?q=` (YouTube + SoundCloud, Spotify si clés). Si YouTube bloque le serveur, le même son est cherché sur SoundCloud (`soundcloud_twin`).
- **Explorer** : les sons publiés (`/api/posts`) sont relus à chaque visite ; `POSTS_GEN` jette les réponses arrivées après une remise à zéro ; « Voir » après une publication amène le son à l'écran (`POSTS_FOCUS`). Le flux des playlists est rafraîchi s'il a plus d'une minute.
- **Recommandations** : `POST /api/recs` reçoit les artistes / mots les plus présents de la bibliothèque (`recSeeds`) et les liens à exclure, classe les sons publiés (artiste exact +3, artiste dans le titre +2, mot du titre +1, puis j'aime) et renvoie des playlists publiques proches (page 0). Bibliothèque vide = tendances (`mode: "trend"`). Rien n'est enregistré, jamais d'e-mail (`post_json`, `pl_summary`). Explorer affiche « Suggestions pour toi » (`#recs`, cache 5 min). **Radio** (menu du lecteur, `S.player.radio`, désactivée par défaut) : à la fin de la file, `radioMore` ajoute 5 sons proches (écoutés en passant, `tmp`) et passe avant « répéter la file ».
- **Quoi de neuf ?** : liste `NEWS` dans `index.html` (seulement ce qui est vraiment en ligne, texte simple traduit). Montrée une fois aux habitués quand `NEWV` dépasse la valeur gardée (`localStorage` `tknew`), jamais par-dessus un lien direct ni à un nouveau visiteur (lui voit `welcome`), et toujours dans Profil > Réglages et données. Ajouter une nouveauté = une ligne dans `NEWS` + augmenter `NEWV` + traduction.
- **Synchro de la bibliothèque** : après un envoi, l'appli prend comme base ce que le serveur a vraiment gardé (`data` de la réponse), pas ce qu'elle a envoyé, sinon un son refusé passerait pour une suppression. Playlists limitées à 1500 sons des deux côtés (`plUrls`).
- **Paiement** : Stripe.js (`js.stripe.com`) ne se charge qu'au premier paiement lancé (`loadStripe`) ; un échec (hors ligne, bloqueur) laisse réessayer.
- **Sons d'interface** : `sfx(nom)` synthétise de petits sons (aucun fichier), un au plus toutes les 70 ms, comptés à partir du son joué (créer la carte son peut prendre ~100 ms au premier).
- **Premium visible** : les listes (Explorer, recherche, sons publiés) renvoient `premium` et la décoration d'avatar (`list_badge` dans `server.py`, jamais l'e-mail). L'appli met une lueur dorée (`.av.prem`) et le badge « Premium », seulement quand l'abonnement est actif sur le serveur (`PAYWALL`).
- **Cosmétiques Premium** : listes `COS_*` dans `server.py` et `COSB/COSF/COSE/COSN/COSG` dans `index.html` (+ règles CSS `bn-`, `fr-`, `pfx-`, `nf-`). Ajouter une décoration = les trois endroits + sa traduction.
- **Statut Discord** : `npExpose()` écrit ce qui joue sur `<html data-np-title|artist|cover|state|start|end>` ; le module PreMiD de `discord/premid/` le lit. Impossible sur téléphone.
- **Téléphones** : champs de saisie à 16 px minimum sur mobile (sinon Safari iPhone zoome), `<meta name="color-scheme" content="dark light">` (pas de mode sombre forcé Samsung / Chrome), manifest avec `id`.
- **Sélection multiple** : appui long ou bouton ☑ (`SEL`, `selStart`, `selEnd`).
- **Menus** : `openSh` / `closeSh` avec des minuteurs `SHT`.
- **Premium 2,99 €/mois** (Stripe) : sans pub, hors ligne, presets, thèmes, cosmétiques.

## Français / English

- Langue : choix enregistré (`localStorage` `tklang` + cookie `tk_lang`), sinon celle du téléphone ou du navigateur (français, ou anglais pour toute autre langue). Réglage dans Profil → « Langue ».
- **Appli** : la phrase française est la clé. Tout texte visible passe par la fonction `_t` (cases `{0}`, `{1}` pour les valeurs) ; sa traduction va dans `lang-en.js`. Le texte écrit directement dans la page est traduit au chargement (`translatePage`) ; un bloc avec gras ou lien porte `data-th`. `plural()` gère « son / sound ». Dates et tris suivent `LOC`.
- **Serveur** : `err("Phrase")` et `T("Phrase {x}", x=…)` traduisent selon l'en-tête `X-Lang` (envoyé par `api()`), le cookie `tk_lang` ou `Accept-Language`. Toute nouvelle phrase va dans `i18n_server.EN`.
- Les tests `test_every_server_message_has_a_translation`, `test_every_translated_app_string_is_in_the_dictionary` et `test_no_french_is_left_on_any_english_screen` attrapent un oubli.

## Ne pas renommer (sinon perte de données ou déconnexion)

- la clé `localStorage` `"tiksound"` (toute la bibliothèque) et la base `indexedDB.open("tiksound")` (sons hors ligne) ;
- l'import de sauvegarde : il accepte `"tiksound"` (ancien) et `"tikwave"` (nouveau) ;
- le fichier secret par défaut `.tiksound_secret`, la base locale `tiksound.db`, le cookie de session `tk_sess`, l'en-tête `X-Tiksound` (le service worker s'en sert pour savoir quoi garder) ;
- les clés d'accès déjà données (`TKS-…`) restent valables ; les nouvelles commencent par `TKW-` ;
- le bucket R2 `tiksound`, le dépôt GitHub, le service Render et les variables d'environnement existantes.

Les données du navigateur sont rangées par adresse : sur tikwave.fr, un visiteur repart d'une bibliothèque vide (son compte resynchronise titres et playlists, les sons hors ligne sont à retélécharger) et doit se reconnecter. Sur l'ancienne adresse, un bandeau (`#moved`, `showMoved`) explique comment exporter sa sauvegarde et l'importer sur tikwave.fr. Ne pas rediriger l'ancienne adresse tant que les gens n'ont pas eu le temps de le faire.

`MAIL_FROM` par défaut : `TikWave <no-reply@tikwave.fr>`. Pour envoyer depuis tikwave.fr, le domaine doit d'abord être vérifié chez Resend ou Brevo (lignes DNS à ajouter chez Gandi).

## Limites connues

- Le comportement réel en arrière-plan sur iPhone et Android n'a été vérifié qu'en émulation.
- YouTube peut bloquer Render (« confirm you're not a bot ») : il faut alors `YTDLP_COOKIES` ou `YTDLP_PROXY`.
- Télécharger depuis YouTube, SoundCloud, Spotify ou TikTok va contre leurs conditions et comporte un risque juridique. AdSense et PreMiD peuvent refuser le site pour cette raison. Le nom « TikWave » ressemble à « TikTok » : risque de marque à vérifier (INPI, EUIPO).
- Les traductions anglaises ont été écrites d'un coup : à faire relire.
- Fonctions évoquées mais pas faites : abonnements entre utilisateurs, commentaires, notifications, messages privés, playlists collaboratives, récap annuel.
