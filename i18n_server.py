"""Français / English côté serveur.

La phrase française est la clé : T("Phrase") la renvoie telle quelle en français, ou sa traduction en anglais.
Les parties variables s'écrivent {nom} et se passent en arguments : T("Patiente {s} s ...", s=wait).
Toute nouvelle phrase passée à err() ou T() doit être ajoutée ici (tests/test_i18n.py le vérifie).
"""
from flask import has_request_context, request

LANGS = ("fr", "en")


def lang():
    """Langue de la requête : ?lang= > en-tête X-Lang (l'appli) > cookie du choix > navigateur. Sans indication : français."""
    if not has_request_context():
        return "fr"
    for v in (request.args.get("lang"), request.headers.get("X-Lang"), request.cookies.get("tk_lang")):
        if v in LANGS:
            return v
    best = next((tag for tag, q in request.accept_languages if q > 0 and tag != "*"), "")
    return "en" if best and not best.lower().startswith("fr") else "fr"


def T(fr, **kw):
    s = EN.get(fr, fr) if lang() == "en" else fr
    return s.format(**kw) if kw else s


EN = {
    # comptes
    "Non authentifié": "Not signed in",
    "Aucun code en attente. Demande un nouveau code.": "No pending code. Ask for a new one.",
    "Ce code a expiré. Demande un nouveau code.": "This code has expired. Ask for a new one.",
    "Trop d'essais. Demande un nouveau code.": "Too many attempts. Ask for a new code.",
    "Code incorrect ({n} essais restants)": "Incorrect code ({n} tries left)",
    "Code incorrect (1 essai restant)": "Incorrect code (1 try left)",
    "3 à 24 caractères : lettres, chiffres, _ ou -": "3 to 24 characters: letters, numbers, _ or -",
    "Ce pseudo est réservé": "This username is reserved",
    "Ce pseudo est déjà pris": "This username is already taken",
    "Adresse email invalide": "Invalid email address",
    "Mot de passe trop court (8 caractères minimum)": "Password too short (8 characters minimum)",
    "Mot de passe trop long": "Password too long",
    "Un compte existe déjà avec cet email": "An account already exists with this email",
    "Patiente {s} s avant de redemander un code": "Wait {s} s before asking for another code",
    "Trop de tentatives, réessaie plus tard": "Too many attempts, try again later",
    "L'envoi d'email n'est pas configuré sur le serveur (ajoute BREVO_API_KEY ou RESEND_API_KEY et MAIL_FROM).":
        "Email sending isn't set up on the server (add BREVO_API_KEY or RESEND_API_KEY and MAIL_FROM).",
    "Impossible d'envoyer l'email de vérification. Réessaie plus tard.": "Couldn't send the verification email. Try again later.",
    "Ce pseudo vient d'être pris. Recommence l'inscription avec un autre pseudo.":
        "This username was just taken. Sign up again with another one.",
    "Ce pseudo ou cet email vient d'être utilisé. Recommence.": "This username or email was just used. Please start over.",
    "Aucune inscription en attente pour cet email. Recommence l'inscription.": "No pending sign-up for this email. Please sign up again.",
    "Trop de demandes, réessaie plus tard": "Too many requests, try again later",
    "Impossible d'envoyer l'email. Réessaie plus tard.": "Couldn't send the email. Try again later.",
    "Trop de tentatives. Réessaie dans quelques minutes.": "Too many attempts. Try again in a few minutes.",
    "Ton compte n'est pas encore vérifié. Entre le code reçu par email.": "Your account isn't verified yet. Enter the code we emailed you.",
    "Email ou mot de passe incorrect": "Wrong email or password",
    "Compte introuvable": "Account not found",
    "Utilisateur introuvable": "User not found",
    "Couleur invalide": "Invalid color",
    "Avatar invalide": "Invalid avatar",
    "Les cosmétiques sont réservés à Premium": "Cosmetics are for Premium members only",
    "Image invalide ou trop lourde": "Invalid image, or too large",
    "Les GIF animés sont réservés à Premium": "Animated GIFs are for Premium members only",
    "Mot de passe incorrect": "Wrong password",
    "Impossible de résilier ton abonnement pour le moment : ton compte n'a pas été supprimé. Réessaie dans un instant.":
        "We couldn't cancel your subscription right now, so your account was not deleted. Try again in a moment.",
    # playlists, sons, bibliothèque
    "Nom requis": "Name required",
    "Maximum {n} playlists publiques": "Maximum {n} public playlists",
    "Playlist introuvable": "Playlist not found",
    "Interdit": "Not allowed",
    "Son invalide": "Invalid sound",
    "Trop d'actions, patiente un peu": "Too many actions, wait a moment",
    "Son invalide (lien TikTok requis)": "Invalid sound (TikTok link required)",
    "Trop de publications, réessaie plus tard": "Too many posts, try again later",
    "Maximum 1000 sons publiés": "Maximum 1000 published sounds",
    "Introuvable": "Not found",
    "Trop de synchronisations, patiente un peu": "Too many syncs, wait a moment",
    "Bibliothèque modifiée sur un autre appareil": "Library changed on another device",
    "Son TikTok": "TikTok sound",
    # abonnement et clés
    "L'abonnement n'est pas disponible pour le moment": "Subscription isn't available right now",
    "Trop d'essais, réessaie plus tard": "Too many tries, try again later",
    "Tu es administrateur : le Premium est déjà actif": "You're an administrator: Premium is already active",
    "Tu as déjà un abonnement : gère-le depuis ton profil": "You already have a subscription: manage it from your profile",
    "Le paiement est momentanément indisponible, réessaie dans un instant": "Payment is temporarily unavailable, try again in a moment",
    "Aucun abonnement à gérer": "No subscription to manage",
    "Le portail est momentanément indisponible, réessaie dans un instant": "The billing portal is temporarily unavailable, try again in a moment",
    "Webhook non configuré": "Webhook not configured",
    "Signature invalide": "Invalid signature",
    "Évènement illisible": "Unreadable event",
    "Traitement impossible": "Couldn't process the event",
    "Réservé à l'administrateur": "Administrator only",
    "Clé introuvable": "Key not found",
    "Clé invalide ou expirée": "Invalid or expired key",
    "Tu as déjà un accès Premium offert": "You already have gifted Premium access",
    "Cette clé a déjà été utilisée le nombre maximum de fois": "This key has already been used the maximum number of times",
    # signalements
    "Trop de signalements envoyés, réessaie plus tard": "Too many reports sent, try again later",
    "Indique le lien du contenu à signaler": "Enter the link to the content you're reporting",
    "Adresse e-mail invalide": "Invalid email address",
    "Statut invalide": "Invalid status",
    "l'adresse de l'éditeur n'est pas encore renseignée.": "the publisher's address hasn't been filled in yet.",
    # recherche et conversion
    "Trop d'imports d'un coup, patiente un peu": "Too many imports at once, wait a moment",
    "Lien Spotify invalide (playlist, album ou titre)": "Invalid Spotify link (playlist, album or track)",
    "Spotify a refusé la lecture de cette playlist (elle doit être publique, pas privée).":
        "Spotify refused to share this playlist (it must be public, not private).",
    "Impossible de lire ce lien Spotify. Vérifie que la playlist est publique.": "Couldn't read this Spotify link. Check that the playlist is public.",
    "Écris au moins 2 lettres": "Type at least 2 letters",
    "Trop de recherches d'un coup, patiente un peu": "Too many searches at once, wait a moment",
    "Trop de conversions d'un coup, patiente un peu": "Too many conversions at once, wait a moment",
    "Lien TikTok invalide": "Invalid TikTok link",
    "Aucune version audio trouvée pour « {titre} ».": "No audio version found for “{titre}”.",
    "Ce son est trop long (plus de 20 min) ou en direct.": "This sound is too long (over 20 min) or is a live stream.",
    "YouTube bloque le serveur en ce moment et ce son est introuvable sur SoundCloud. Essaie un résultat SoundCloud.":
        "YouTube is blocking the server right now and this sound isn't on SoundCloud. Try a SoundCloud result.",
    "Impossible de récupérer ce son pour le moment. Essaie un autre résultat.": "Couldn't get this sound right now. Try another result.",
    "Impossible de récupérer le son. Vérifie que le lien est public.": "Couldn't get the sound. Check that the link is public.",
    "Le stockage en ligne ne répond pas, réessaie dans un instant.": "Online storage isn't responding, try again in a moment.",
    # e-mails
    "{code} est ton code TikWave": "{code} is your TikWave code",
    "Bienvenue": "Welcome",
    "Bienvenue {pseudo}": "Welcome {pseudo}",
    "Voici ton code pour vérifier ton adresse email et activer ton compte TikWave :":
        "Here is your code to verify your email address and activate your TikWave account:",
    "{code} : réinitialise ton mot de passe TikWave": "{code}: reset your TikWave password",
    "Mot de passe oublié ?": "Forgot your password?",
    "Voici ton code pour choisir un nouveau mot de passe :": "Here is your code to choose a new password:",
    "Ce code expire dans {min} minutes. Si tu n'es pas à l'origine de cette demande, ignore simplement cet email.":
        "This code expires in {min} minutes. If you didn't ask for it, just ignore this email.",
    # aperçus de liens partagés
    "Playlist de {n} sons par @{pseudo}. Écoute-la sur TikWave.": "A playlist of {n} sounds by @{pseudo}. Listen on TikWave.",
    "Playlist de 1 son par @{pseudo}. Écoute-la sur TikWave.": "A playlist with 1 sound by @{pseudo}. Listen on TikWave.",
    "@{pseudo} sur TikWave": "@{pseudo} on TikWave",
    "Ses playlists de sons TikTok.": "Their TikTok sound playlists.",
    "TikWave · les sons TikTok, sans la vidéo": "TikWave · TikTok sounds, without the video",
    "Colle un lien TikTok, garde uniquement le son et écoute-le en playlist, écran éteint.":
        "Paste a TikTok link, keep only the sound and play it in a playlist with the screen off.",
}
