# 🤖 Bot de Planification d'Entraînements pour Discord

Ce bot Discord est conçu pour simplifier l'organisation des entraînements de votre club sportif. Il gère les inscriptions (RSVP), crée des fils de discussion dédiés, envoie des rappels automatiques et nettoie les anciens événements.

---

## 📖 Guide Utilisateur (Pour les Membres)

En tant que membre, votre interaction est très simple.

### Répondre à une invitation d'entraînement

Lorsque les organisateur·rice·s publient un nouvel entraînement, vous verrez un message s'afficher dans le salon avec trois boutons :

* **`✅ Je viens`**
    * Votre nom est ajouté à la liste des participant·e·s (`✅ Présent·e·s`).
    * Vous êtes automatiquement ajouté·e au fil de discussion 💬 de l'événement.
* **`❓ Je ne sais pas`**
    * Votre nom est ajouté à la liste des indécis·e·s (`❓ Indécis·e·s`).
    * Vous êtes également ajouté·e au fil de discussion 💬.
* **`❌ Je ne viens pas`**
    * Votre nom est ajouté à la liste des absent·e·s (`❌ Absent·e·s`).
    * Si vous étiez dans le fil de discussion, vous en serez automatiquement retiré·e.

### Changer d'avis

Vous pouvez modifier votre réponse à tout moment en cliquant simplement sur un autre bouton. Le bot mettra à jour votre statut sur le message principal et ajustera votre présence dans le fil de discussion.

Si un événement est marqué comme **"🚫 ANNULÉ"**, les boutons seront bloqués.

---

## 🛠️ Guide Administrateur·rice (Utilisation)

En tant qu'administrateur·rice, vous disposez de commandes pour créer et gérer les événements.

### Créer un événement via l'Assistant (Recommandé)

La méthode la plus simple est d'utiliser l'assistant en messages privés.

1.  Dans n'importe quel salon, tapez la commande : `/creer_wizard`
2.  Le bot vous enverra un **message privé**. Répondez aux 6 questions posées :
    * **Date** (Format `AAAA-MM-JJ`)
    * **Heure** (Format `HH:MM:SS`, heure de Paris)
    * **Détails** (Lieu, programme, etc.)
    * **Récurrence** (Choisissez : `Aucune`, `Hebdomadaire`, `Mensuelle`)
    * **Garder le fil** (Choisissez : `Non (supprimer)` ou `Oui (archiver)`)
    * **Groupe Cible** (Optionnel : Mentionnez un ou plusieurs rôles, ex: `@RoleA @RoleB`, ou répondez `aucun`)
3.  Une fois terminé, le bot confirmera en MP et publiera l'événement dans le **salon où vous avez lancé la commande `/creer_wizard`**.

### Créer un événement via Commande Rapide

Pour une création rapide (sans récurrence mensuelle), vous pouvez utiliser :

`/creer_entrainement date:AAAA-MM-JJ time:HH:MM:SS details:Vos détails recurrent:True/False target_group:@Role garder_le_fil:True/False`

* `recurrent:True` équivaut à une récurrence **hebdomadaire**. Mettez `False` ou omettez pour un événement unique.

### Annuler un événement (Nouveau)

Cette commande bloque les inscriptions pour un événement (ex: météo) **sans le supprimer**. Le fil de discussion reste actif pour communiquer l'annulation.

1.  **Copiez l'ID du message** de l'événement (activez le Mode Développeur, clic droit > "Copier l'ID du message").
2.  Lancez la commande : `/annuler_evenement message_id: [ID copié]`
3.  **Effet :** Le bot ajoutera "🚫 ANNULÉ" au titre de l'embed, bloquera les nouvelles inscriptions, et enverra un message d'annulation dans le fil.

### Supprimer un événement incorrect

Si un événement a été créé par erreur (par exemple par une mauvaise récurrence passée) :

1.  **Copiez l'ID du message** de l'événement.
2.  Lancez la commande (seul·e·s les admins peuvent) :
    `/supprimer_evenement message_id: [ID que vous avez copié]`
3.  **Effet :** Le bot supprimera le message, le fil associé, et l'entrée dans la base de données (annulant sa récurrence et ses rappels).

---

## 🚀 Installation et Lancement du Bot (Pour l'Hébergeur·euse)

Suivez ces étapes pour héberger et lancer le bot vous-même.

### 1. Prérequis

* **Python 3.9+** installé sur votre machine ([python.org](https://www.python.org/)).
* Un compte Discord.

### 2. Création du Bot sur le Portail Développeur Discord

1.  Allez sur <https://discord.com/developers/applications>.
2.  Cliquez sur **"New Application"** et donnez un nom.
3.  Allez dans l'onglet **"Bot"**.
4.  **Important :** Activez les **"Privileged Gateway Intents"** :
    * ✅ **SERVER MEMBERS INTENT**
    * ✅ **MESSAGE CONTENT INTENT**
5.  Cliquez sur **"Reset Token"**, confirmez, et **copiez le jeton (token)** affiché. **Ne le partagez jamais !**

### 3. Invitation du Bot sur votre Serveur

1.  Allez dans **"OAuth2"** > **"URL Generator"**.
2.  Cochez les **Scopes** : `bot` et `application.commands`.
3.  Définissez les **Bot Permissions** suivantes :
    * `View Channels`
    * `Send Messages`
    * `Send Messages in Threads`
    * `Create Public Threads`
    * `Manage Threads` (pour ajouter/retirer des membres)
    * `Read Message History` (pour le nettoyage)
    * `Manage Messages` (pour la purge avant récurrence)
4.  **Copiez l'URL** générée en bas.
5.  Collez l'URL dans votre navigateur et invitez le bot sur le serveur souhaité.

### 4. Préparation des Fichiers et Installation des Librairies

1.  **Créez un dossier** pour votre bot.
2.  **Téléchargez le code** (`bot.py`) et placez-le dans ce dossier.
3.  **Ouvrez un terminal** (Invite de commandes, PowerShell, Terminal...) et naviguez jusqu'à ce dossier (`cd chemin/vers/le/dossier`).
4.  **Installez les librairies Python requises** :
    ```bash
    pip install discord.py python-dateutil
    ```
    *(Si vous utilisez Python 3.8 ou inférieur, installez aussi : `pip install backports.zoneinfo`)*

### 5. Configuration du Jeton (Token)

1.  Ouvrez le fichier `bot.py` avec un éditeur de texte.
2.  Trouvez la ligne : `BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"`
3.  Remplacez `"YOUR_BOT_TOKEN_HERE"` par le **jeton** que vous avez copié à l'étape 2.5.
4.  Enregistrez le fichier.

### 6. Lancement du Bot

1.  Retournez dans votre terminal (toujours dans le dossier du bot).
2.  Lancez le script Python :
    ```bash
    python bot.py
    ```
3.  Le terminal devrait afficher :
    ```
    Connecté en tant que [NomDeVotreBot]
    Le bot est prêt !
    ```

Le bot est maintenant en ligne !

**Note :** Le bot s'arrêtera si vous fermez le terminal. Pour un fonctionnement continu (24/7), vous devez l'héberger sur un serveur ou un service d'hébergement.

---

## ⚙️ Fonctionnement Automatique du Bot

* **Rappel J-3 :** Si un `target_group` est défini, un rappel est envoyé dans le salon 3 jours avant l'événement.
* **Rappel H-24 :** Un rappel est envoyé dans le *fil de discussion* 24 heures avant l'événement, mentionnant les participant·e·s et les indécis·e·s.
* **Nettoyage (Cleanup) :** 24 heures *après* l'heure de début de l'événement :
    * Le bot publie un rapport final dans le fil de discussion.
    * **Si `garder_le_fil` est `False` (défaut) :** Le fil est supprimé et le message principal aussi. Si l'événement est récurrent, le bot purge d'abord ses anciens messages du salon avant de créer le suivant.
    * **Si `garder_le_fil` est `True` :** Le fil est juste archivé, et le message principal est modifié (l'embed est mis à jour en "Rapport final") pour désactiver les boutons.
    * L'événement est supprimé de la base de données.
* **Récurrence :** Si l'événement est récurrent (`Hebdomadaire` ou `Mensuelle`), le bot crée et publie le nouvel événement juste après le nettoyage.