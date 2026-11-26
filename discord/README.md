# Statut Discord « Écoute TikWave »

## Ce qui est possible

Un site web ne peut pas changer le statut Discord tout seul : Discord ne l'autorise qu'à un programme installé sur l'ordinateur. Le relais le plus utilisé est **PreMiD**, une extension gratuite pour Chrome, Firefox ou Edge, accompagnée d'un petit programme.

- **Sur ordinateur**, avec l'appli Discord et PreMiD installés : ton statut affiche « Écoute TikWave », avec le titre, l'artiste, la pochette et le temps restant. Il disparaît quand la musique est en pause.
- **Sur téléphone** (iPhone, Samsung, Android), c'est impossible. Discord ne laisse aucun site ni aucune appli web modifier le statut depuis un téléphone. Seules les intégrations officielles comme Spotify le peuvent.

## Ce qui est déjà fait

- Pendant l'écoute, l'appli écrit ce qui joue sur la balise `<html>` : `data-np-title`, `data-np-artist`, `data-np-cover`, `data-np-state`, `data-np-start` et `data-np-end` (fonction `npExpose` dans `index.html`). La pochette n'est donnée que si c'est une adresse publique : une image rangée sur l'appareil n'est jamais transmise.
- Le module PreMiD est prêt dans `premid/`. Il lit ces informations et règle le statut.

## Ce qu'il reste à faire, par toi

1. **Créer l'application Discord** sur https://discord.com/developers/applications : « New Application », nom « TikWave ». Ajoute le logo dans « Rich Presence ». Copie l'« Application ID » dans `premid/presence.ts`, à la place de `REMPLACER_PAR_L_ID_DE_L_APPLICATION_DISCORD`.
2. Mets ton pseudo et ton identifiant Discord dans `premid/metadata.json`.
3. **Attendre fin janvier 2027.** PreMiD refuse les domaines de moins de 2 mois, et tikwave.fr a été acheté le 26 novembre 2026.
4. Proposer le module à PreMiD : copier `premid/` dans `websites/T/TikWave/` du dépôt GitHub PreMiD/Activities, lancer `npx pmd build "TikWave" --validate`, puis ouvrir une demande de fusion (pull request). Leurs règles demandent un logo de 512 × 512 hébergé sur Imgur.

Attention : PreMiD refuse les sites illégaux. Un site qui télécharge des sons TikTok ou YouTube risque d'être refusé.

En attendant, tu peux essayer le module sur ton propre ordinateur, sans l'envoyer, avec `npx pmd dev "TikWave"` (mode développeur de PreMiD).
