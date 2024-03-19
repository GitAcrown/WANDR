# NEROSYS
Base légère pour bot Discord modulaire.

## Fonctionnalités
- Utilise discordpy >=2.10 pour le bot et ses modules
- Organisation des scripts et modules par dossiers distincts
- Wrapper SQLite pour faciliter la gestion des données locales (common/dataio.py)
- Contient des fonctionnalités utiles (common/utils) comme la recherche par similarité ou des fonctions d'affichage

## Configuration
### Personnaliser votre bot
Vous pouvez modifier ce dont vous avez besoin (prefixe et description) dans bot.py et modifier les loggers dans chaque module de base (remplacez NEROSYS par le nom désiré)

### Créez un fichier .env
Vous devez créer un fichier .env qui contient, a minima:
- `TOKEN=` pour votre token que vous trouverez [sur la page developpeur de votre bot](https://discord.com/developers/applications)
- `APP_ID=` qui est l'identifiant de l'application (que vous trouverez aussi dans la page de votre bot), utilisé pour générer le lien d'invitation
- `OWNER=` qui est votre identifiant Discord à vous
- `PERMISSIONS_INT=` qui est un nombre que vous obtiendrez dans l'onglet "Bot" de la page de votre bot, cochez les bonnes permissions et copiez/coller le nombre obtenu
Ce fichier doit être placé à la racine de ce dossier, comme bot.py

### Créez vos propres modules
Les modules se trouvent dans le dossier *cogs*, qui est séparé en sous-dossiers pour chaque module (ce qui permet d'organiser les données lorsqu'elles sont crées).
Ce bot est livré avec un module ***example*** qui vous montre, de manière commentée, les bases pour créer un module simple (ici, un module permettant d'ajouter des triggers de tchat). 
N'hésitez pas à rejoindre le serveur ci-dessous pour plus d'aide sur la création de vos modules.

## Obtenir de l'aide
Pour plus d'infos concernant sa configuration, veuillez consulter le [serveur Discord de développement](discord.gg/65WFUXsgtq)
