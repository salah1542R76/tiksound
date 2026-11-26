// Statut Discord « Écoute TikWave » pour l'extension PreMiD (https://premid.app).
// L'appli écrit ce qui joue sur <html data-np-title / -artist / -cover / -state / -start / -end> (fonction npExpose dans index.html).
// À vérifier au moment de l'envoi : `npx pmd build "TikWave" --validate` (dépôt PreMiD/Activities).
const presence = new Presence({
  clientId: 'REMPLACER_PAR_L_ID_DE_L_APPLICATION_DISCORD', // Discord Developer Portal → New Application « TikWave » → Application ID
})

const LOGO = 'https://tikwave.fr/icon-512.png'

presence.on('UpdateData', async () => {
  const d = document.documentElement.dataset
  if (!d.npTitle || d.npState !== 'playing') {
    presence.clearActivity()
    return
  }
  const data: PresenceData = {
    type: ActivityType.Listening,
    largeImageKey: d.npCover || LOGO,
    details: d.npTitle,
    state: d.npArtist || 'TikWave',
    startTimestamp: Math.floor(Number(d.npStart) / 1000),
  }
  if (d.npEnd)
    data.endTimestamp = Math.floor(Number(d.npEnd) / 1000)
  presence.setActivity(data)
})
