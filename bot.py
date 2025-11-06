import discord
from discord.ext import commands, tasks
import sqlite3
import datetime
import calendar
from zoneinfo import ZoneInfo
import asyncio
from dateutil.relativedelta import relativedelta
import urllib.parse
import functools # Ajout pour asyncio.to_thread

# ====================================================================
# 1. CONFIGURATION ET INITIALISATION
# ====================================================================
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE" 
FRENCH_TZ = ZoneInfo("Europe/Paris") 
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ====================================================================
# 2. CONFIGURATION BDD ET FONCTIONS UTILITAIRES
# ====================================================================

DB_NAME = "club_attendance.db"
DB_TIMEOUT = 10.0 # Timeout pour éviter les "database is locked"

# MODIFIÉ : init_db est maintenant synchrone et appelée une seule fois au démarrage.
def init_db():
    """Initialise la base de données (exécutée de manière synchrone au démarrage)."""
    conn = sqlite3.connect(DB_NAME, timeout=DB_TIMEOUT)
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS events (
        message_id INTEGER PRIMARY KEY, thread_id INTEGER, channel_id INTEGER, 
        event_date TEXT, event_time TEXT, details TEXT,
        is_recurrent INTEGER DEFAULT 0, 
        target_group TEXT, reminder_3d_sent INTEGER DEFAULT 0, 
        reminder_24h_sent INTEGER DEFAULT 0, keep_thread INTEGER DEFAULT 0,
        recurrence_type TEXT DEFAULT 'none', is_cancelled INTEGER DEFAULT 0,
        reminder_dm_sent INTEGER DEFAULT 0,
        duration_hours REAL DEFAULT 2.0  -- AJOUT : Durée de l'événement
    )''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER, user_id INTEGER,
        user_name TEXT, status TEXT, UNIQUE(message_id, user_id)
    )''')
    
    # --- Migrations de la BDD ---
    all_columns = [col[1] for col in cursor.execute("PRAGMA table_info(events)").fetchall()]
    if 'recurrence_type' not in all_columns:
        print("Migration BDD : Ajout 'recurrence_type'")
        cursor.execute("ALTER TABLE events ADD COLUMN recurrence_type TEXT DEFAULT 'none'")
        cursor.execute("UPDATE events SET recurrence_type = 'weekly' WHERE is_recurrent = 1")
    if 'is_cancelled' not in all_columns:
        print("Migration BDD : Ajout 'is_cancelled'")
        cursor.execute("ALTER TABLE events ADD COLUMN is_cancelled INTEGER DEFAULT 0")
    if 'reminder_dm_sent' not in all_columns:
        print("Migration BDD : Ajout 'reminder_dm_sent'")
        cursor.execute("ALTER TABLE events ADD COLUMN reminder_dm_sent INTEGER DEFAULT 0")
    # AJOUT : Migration pour la durée
    if 'duration_hours' not in all_columns:
        print("Migration BDD : Ajout 'duration_hours'")
        cursor.execute("ALTER TABLE events ADD COLUMN duration_hours REAL DEFAULT 2.0")
        
    conn.commit()
    conn.close()

# MODIFIÉ : log_attendance est divisée en une partie synchrone et un wrapper async
def _log_attendance_sync(message_id, user_id, user_name, status):
    """Partie synchrone de l'enregistrement de la présence."""
    conn = sqlite3.connect(DB_NAME, timeout=DB_TIMEOUT)
    cursor = conn.cursor()
    cursor.execute('''
    REPLACE INTO attendance (message_id, user_id, user_name, status)
    VALUES (?, ?, ?, ?)
    ''', (message_id, user_id, user_name, status))
    conn.commit()
    conn.close()

async def log_attendance(message_id, user_id, user_name, status):
    """Wrapper Asynchrone : Enregistre la présence dans un thread séparé."""
    await asyncio.to_thread(_log_attendance_sync, message_id, user_id, user_name, status)

# MODIFIÉ : get_attendance_summary
def _get_attendance_summary_sync(message_id):
    """Partie synchrone de la récupération du résumé."""
    conn = sqlite3.connect(DB_NAME, timeout=DB_TIMEOUT)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_name, status, user_id FROM attendance
        WHERE message_id = ? GROUP BY user_id
    """, (message_id,))
    attendance_data = cursor.fetchall()
    conn.close()
    
    coming = [(name, user_id) for name, status, user_id in attendance_data if status == "Coming"]
    maybe = [(name, user_id) for name, status, user_id in attendance_data if status == "Maybe"]
    not_coming = [(name, user_id) for name, status, user_id in attendance_data if status == "Not Coming"]
    return {"coming": coming, "maybe": maybe, "not_coming": not_coming}

async def get_attendance_summary(message_id):
    """Wrapper Asynchrone : Récupère le résumé des présences."""
    return await asyncio.to_thread(_get_attendance_summary_sync, message_id)

# MODIFIÉ : get_event_state (utilise duration_hours)
def _get_event_state_sync(message_id):
    """Partie synchrone de la récupération de l'état (date/heure/durée)."""
    conn = sqlite3.connect(DB_NAME, timeout=DB_TIMEOUT)
    cursor = conn.cursor()
    # AJOUT : Sélectionne duration_hours
    cursor.execute("SELECT event_date, event_time, is_cancelled, duration_hours FROM events WHERE message_id = ?", (message_id,))
    row = cursor.fetchone()
    conn.close()
    if not row: return (None, False)
    
    date, time, is_cancelled, duration_hours = row
    
    # Assurer une durée par défaut si la BDD est NULL
    if duration_hours is None:
        duration_hours = 2.0 
        
    try:
        naive_dt = datetime.datetime.fromisoformat(f"{date}T{time}")
        local_dt = naive_dt.replace(tzinfo=FRENCH_TZ)
        event_start_utc = local_dt.astimezone(datetime.timezone.utc)
        # MODIFIÉ : Utilise duration_hours au lieu de 2h fixes
        event_end_utc = event_start_utc + datetime.timedelta(hours=duration_hours)
        return (event_end_utc, bool(is_cancelled))
    except Exception as e:
        print(f"Erreur d'analyse BDD (get_event_state) : {e}")
        return (None, False)

async def get_event_state(message_id):
    """Wrapper Asynchrone : Récupère l'état de l'événement."""
    return await asyncio.to_thread(_get_event_state_sync, message_id)

# MODIFIÉ : create_google_calendar_link (utilise duration_hours)
def create_google_calendar_link(event_date, event_time, details, duration_hours):
    """Crée un lien Google Calendar (fonction synchrone, pas d'accès BDD)."""
    try:
        if duration_hours is None:
            duration_hours = 2.0
            
        naive_dt = datetime.datetime.fromisoformat(f"{event_date}T{event_time}")
        start_local = naive_dt.replace(tzinfo=FRENCH_TZ)
        # MODIFIÉ : Utilise duration_hours
        end_local = start_local + datetime.timedelta(hours=duration_hours)
        
        start_utc_str = start_local.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        end_utc_str = end_local.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        
        dates_str = f"{start_utc_str}/{end_utc_str}"
        title = f"Entraînement : {event_date}"
        base_url = "https://www.google.com/calendar/render?action=TEMPLATE"
        params = {"text": title, "dates": dates_str, "details": details, "ctz": "UTC"}
        return f"{base_url}&{urllib.parse.urlencode(params)}"
    except Exception as e:
        print(f"Erreur création lien Google Calendar : {e}")
        return None

# MODIFIÉ : Fonction BDD pour insérer un nouvel événement
def _db_insert_event_sync(message_id, thread_id_to_save, channel_id, date, time, details, recurrence_type, target_group, garder_le_fil, duration_hours):
    """Partie synchrone de l'insertion d'un nouvel événement."""
    is_recurrent_int = 1 if recurrence_type != 'none' else 0
    conn = sqlite3.connect(DB_NAME, timeout=DB_TIMEOUT)
    cursor = conn.cursor()
    cursor.execute('''
    INSERT INTO events (message_id, thread_id, channel_id, event_date, event_time, details, 
                        is_recurrent, target_group, reminder_3d_sent, reminder_24h_sent, 
                        keep_thread, recurrence_type, is_cancelled, duration_hours)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, 0, ?)
    ''', (message_id, thread_id_to_save, channel_id, date, time, details, 
          is_recurrent_int, target_group, int(garder_le_fil), recurrence_type, duration_hours))
    conn.commit()
    conn.close()

# Appel synchrone de l'initialisation de la BDD au démarrage du script
init_db()

# ====================================================================
# 3. LOGIQUE DES BOUTONS (VIEWS) -- TEXTE INCLUSIF
# ====================================================================
class TrainingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    async def update_message(self, interaction: discord.Interaction):
        # MODIFIÉ : Appel async BDD
        summary = await get_attendance_summary(interaction.message.id)
        
        coming_list = "\n".join([f"• {name}" for name, user_id in summary["coming"]]) or "— Personne pour l'instant —"
        maybe_list = "\n".join([f"• {name}" for name, user_id in summary["maybe"]]) or "— Personne pour l'instant —"
        not_coming_list = "\n".join([f"• {name}" for name, user_id in summary["not_coming"]]) or "— Personne pour l'instant —"
        
        if not interaction.message or not interaction.message.embeds:
            print("Impossible de mettre à jour le message (probablement supprimé).")
            return
            
        original_embed = interaction.message.embeds[0]
        new_embed = discord.Embed(title=original_embed.title, description=original_embed.description, color=original_embed.color)
        
        for field in original_embed.fields:
            if (not field.name.startswith("✅ Présent·e·s") and not field.name.startswith("❓ Indécis·e·s") and not field.name.startswith("❌ Absent·e·s")):
                    new_embed.add_field(name=field.name, value=field.value, inline=field.inline)
                    
        new_embed.add_field(name=f"✅ Présent·e·s ({len(summary['coming'])})", value=coming_list, inline=True)
        new_embed.add_field(name=f"❓ Indécis·e·s ({len(summary['maybe'])})", value=maybe_list, inline=True)
        new_embed.add_field(name=f"❌ Absent·e·s ({len(summary['not_coming'])})", value=not_coming_list, inline=True)
        
        try:
            await interaction.message.edit(embed=new_embed, view=self)
        except discord.NotFound:
            print(f"Échec de l'édition du message {interaction.message.id} (n'existe plus).")
        except Exception as e:
            print(f"Erreur inconnue lors de l'édition du message : {e}")

    async def invite_and_update(self, interaction: discord.Interaction, status: str, response_text: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        # MODIFIÉ : Appel async BDD
        await log_attendance(interaction.message.id, interaction.user.id, interaction.user.display_name, status)
        
        try:
            thread = interaction.message.thread
            if thread:
                if status in ["Coming", "Maybe"]:
                    await thread.add_user(interaction.user)
                    response_text += "\n✅ **Vous avez été ajouté·e au fil de discussion privé.**"
                elif status == "Not Coming":
                    await thread.remove_user(interaction.user)
                    response_text += "\n👋 **Vous avez été retiré·e du fil de discussion privé.**"
        except discord.Forbidden:
            print(f"Erreur : Le bot n'a pas la permission de gérer les utilisateurs dans le thread {thread.id}")
            response_text += "\n⚠️ Le bot n'a pas les permissions pour gérer l'accès au thread."
        except Exception as e:
            print(f"Erreur lors de la gestion de l'accès au thread : {e}")
            
        await interaction.followup.send(response_text, ephemeral=True)
        
        try:
            await self.update_message(interaction)
        except Exception as e:
            print(f"Erreur lors de l'update_message (après followup) : {e}")

    @discord.ui.button(label="✅ Je viens", style=discord.ButtonStyle.green, custom_id="coming")
    async def coming_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # MODIFIÉ : Appel async BDD
        event_end_utc, is_cancelled = await get_event_state(interaction.message.id)
        
        if is_cancelled:
            await interaction.response.send_message("Désolé, cet événement a été **annulé**. Les inscriptions sont fermées.", ephemeral=True)
            return
        if not event_end_utc or datetime.datetime.now(datetime.timezone.utc) > event_end_utc:
            await interaction.response.send_message("Désolé, cet événement est déjà terminé.", ephemeral=True)
            return
            
        await self.invite_and_update(interaction, "Coming", "Vous êtes marqué·e comme 'Présent·e'. Rendez-vous là-bas !")

    @discord.ui.button(label="❓ Je ne sais pas", style=discord.ButtonStyle.blurple, custom_id="maybe")
    async def maybe_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # MODIFIÉ : Appel async BDD
        event_end_utc, is_cancelled = await get_event_state(interaction.message.id)
        
        if is_cancelled:
            await interaction.response.send_message("Désolé, cet événement a été **annulé**. Les inscriptions sont fermées.", ephemeral=True)
            return
        if not event_end_utc or datetime.datetime.now(datetime.timezone.utc) > event_end_utc:
            await interaction.response.send_message("Désolé, cet événement est déjà terminé.", ephemeral=True)
            return
            
        await self.invite_and_update(interaction, "Maybe", "Vous êtes marqué·e comme 'Indécis·e'. Merci de mettre à jour si possible !")

    @discord.ui.button(label="❌ Je ne viens pas", style=discord.ButtonStyle.red, custom_id="not_coming")
    async def not_coming_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # MODIFIÉ : Appel async BDD
        event_end_utc, is_cancelled = await get_event_state(interaction.message.id)
        
        if is_cancelled:
            await interaction.response.send_message("Désolé, cet événement a été **annulé**. Les inscriptions sont fermées.", ephemeral=True)
            return
        if not event_end_utc or datetime.datetime.now(datetime.timezone.utc) > event_end_utc:
            await interaction.response.send_message("Désolé, cet événement est déjà terminé.", ephemeral=True)
            return
            
        await self.invite_and_update(interaction, "Not Coming", "Vous êtes marqué·e comme 'Absent·e'. Merci d'avoir prévenu.")


# ====================================================================
# 4. FONCTION PRINCIPALE DE CRÉATION D'ÉVÉNEMENT
# ====================================================================
# MODIFIÉ : Ajout de duration_hours
async def create_event_post(date: str, time: str, details: str, recurrence_type: str, target_group: str, channel: discord.TextChannel, garder_le_fil: bool, duration_hours: float = 2.0):
    try:
        naive_dt = datetime.datetime.fromisoformat(f"{date}T{time}")
        local_dt = naive_dt.replace(tzinfo=FRENCH_TZ)
    except ValueError:
        await channel.send("Erreur : Format de date ou d'heure invalide.", delete_after=10)
        return False
        
    # AJOUT : Mention de la durée
    embed = discord.Embed(title=f"📅 Entraînement : {date}", 
                          description=f"**Heure**: {time} (Heure de Paris)\n**Durée**: {duration_hours}h\n**Lieu/Détails**: {details}", 
                          color=discord.Color.blue())
                          
    if recurrence_type == 'weekly': recurrence_text = " (Récurrent : Hebdomadaire)"
    elif recurrence_type == 'monthly': recurrence_text = " (Récurrent : Mensuel)"
    else: recurrence_text = ""
    
    embed.add_field(name=f"Veuillez répondre{recurrence_text}", value="Cliquez sur un bouton ci-dessous.", inline=False)
    embed.add_field(name="✅ Présent·e·s (0)", value="— Personne pour l'instant —", inline=True)
    embed.add_field(name="❓ Indécis·e·s (0)", value="— Personne pour l'instant —", inline=True)
    embed.add_field(name="❌ Absent·e·s (0)", value="— Personne pour l'instant —", inline=True)
    view = TrainingView()
    
    try:
        message = await channel.send(embed=embed, view=view)
    except discord.Forbidden:
        print(f"ERREUR : Permissions manquantes pour envoyer un message dans le salon {channel.name} ({channel.id})")
        return False 
    except Exception as e:
        print(f"Erreur inconnue lors de l'envoi du message : {e}")
        return False
        
    # Crée un THREAD PRIVÉ
    thread_name = f"💬 Discussion entraînement du {date}"
    try:
        thread = await channel.create_thread(
            name=thread_name,
            message=message, # Attache le thread au message
            auto_archive_duration=1440,
            type=discord.ChannelType.private_thread # Spécifie le type
        )
        await thread.send(f"Utilisez ce fil privé pour discuter des détails de l'entraînement du {date}.")
    except discord.Forbidden:
        print(f"ERREUR : Permissions manquantes pour 'Créer des fils privés' dans {channel.name}")
        await channel.send("⚠️ Erreur : Je n'ai pas la permission de créer des fils privés.", delete_after=10)
        thread = None
    except Exception as e:
        print(f"Erreur création thread : {e}")
        thread = None

    if target_group:
        await channel.send(f"Nouvel entraînement publié ! {target_group} veuillez répondre. ({date} @ {time} Heure de Paris)")

    # Enregistrement BDD (MODIFIÉ : Appel async BDD)
    thread_id_to_save = thread.id if thread else None
    try:
        # MODIFIÉ : Utilise asyncio.to_thread pour l'insertion
        await asyncio.to_thread(
            _db_insert_event_sync,
            message.id, thread_id_to_save, channel.id, date, time, details, 
            recurrence_type, target_group, garder_le_fil, duration_hours
        )
    except Exception as e:
        print(f"ERREUR BDD lors de l'insertion de l'événement : {e}")
        await message.delete() # On tente de supprimer le message si la BDD a échoué
        if thread: await thread.delete()
        return False
        
    return True

# ====================================================================
# 5. ÉVÉNEMENTS DU BOT ET COMMANDES
# ====================================================================
@bot.event
async def on_ready():
    """Confirme la connexion, enregistre les vues persistantes, et lance les tâches."""
    print(f'Connecté en tant que {bot.user}')
    print('Le bot est prêt !')
    
    bot.add_view(TrainingView()) 
    
    await bot.tree.sync() 
    # On vérifie si les tâches ne sont pas déjà lancées avant de les démarrer.
    if not check_for_cleanup.is_running():
        check_for_cleanup.start()
        print("Tâche de nettoyage (check_for_cleanup) démarrée.")
        
    if not check_reminders.is_running():
        check_reminders.start()
        print("Tâche de rappels (check_reminders) démarrée.")



# --- COMMANDE SLASH (RAPIDE) ---
# MODIFIÉ : Ajout de duration_hours
@bot.tree.command(name="creer_entrainement", description="Créer un nouvel entraînement (Heure de Paris)")
@discord.app_commands.describe(
    date="Date (AAAA-MM-JJ)", time="Heure (HH:MM:SS)", details="Détails", 
    duration_hours="Durée en heures (ex: 2.5 pour 2h30)", # AJOUT
    recurrent="[Obsolète] True=Hebdo", target_group="Rôle(s) ou Membre(s) à notifier", 
    garder_le_fil="True=NE PAS supprimer le fil"
)
async def create_training(interaction: discord.Interaction, date: str, time: str, details: str, 
                        duration_hours: float = 2.0, # AJOUT
                        recurrent: bool = False, target_group: str = None, garder_le_fil: bool = False):
    
    await interaction.response.send_message(f"Création de l'entraînement...", ephemeral=True)
    channel = interaction.channel
    recurrence_str = 'weekly' if recurrent else 'none'
    
    # MODIFIÉ : Passe duration_hours
    success = await create_event_post(date, time, details, recurrence_str, target_group, channel, garder_le_fil, duration_hours)
    
    if success:
        await interaction.edit_original_response(content="Entraînement publié avec succès !")
    else:
        await interaction.edit_original_response(content="⚠️ **Échec de la publication.** Vérifiez les logs et les permissions du bot dans ce salon.")

# --- DÉBUT DE L'ASSISTANT (WIZARD) ---
async def ask_text(user: discord.User, question: str, timeout: int = 300) -> str:
    dm = await user.create_dm()
    await dm.send(question)
    def check(m): return m.author == user and m.channel == dm
    try:
        message = await bot.wait_for('message', check=check, timeout=timeout)
        if message.content.lower().strip() in ['aucun', 'non', 'none', '']: return None
        return message.content
    except asyncio.TimeoutError:
        await dm.send("Délai expiré. Relancez la commande."); return None
        
async def ask_choice(user: discord.User, question: str, choices: list[str], timeout: int = 300) -> str:
    dm = await user.create_dm()
    view = discord.ui.View(timeout=timeout)
    result = asyncio.Future()
    
    # Correction de la lambda pour capturer la variable 'choice' correctement
    async def callback(interaction: discord.Interaction, button_label: str):
        await interaction.response.edit_message(content=f"Sélectionné·e : **{button_label}**", view=None)
        if not result.done():
            result.set_result(button_label)

    for choice in choices:
        button = discord.ui.Button(label=choice, style=discord.ButtonStyle.primary)
        # functools.partial est plus sûr que lambda dans les boucles pour ce cas
        button.callback = functools.partial(callback, button_label=choice)
        view.add_item(button)
        
    await dm.send(question, view=view)
    try: 
        return await asyncio.wait_for(result, timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        await dm.send("Délai expiré. Relancez la commande."); return None

@bot.tree.command(name="creer_wizard", description="[ADMIN] Lancer l'assistant de création d'événement en MP.")
@discord.app_commands.checks.has_permissions(administrator=True)
async def creer_wizard(interaction: discord.Interaction):
    user = interaction.user
    original_channel = interaction.channel 
    await interaction.response.send_message(f"Parfait ! Message privé envoyé.", ephemeral=True)
    
    try:
        # MODIFIÉ : Ajout de la durée (Étape 4/7)
        date_str = await ask_text(user, "📅 **Étape 1/7 :** Date ? (AAAA-MM-JJ)")
        if not date_str: return
        time_str = await ask_text(user, "🕒 **Étape 2/7 :** Heure de début ? (HH:MM:SS)")
        if not time_str: return
        details_str = await ask_text(user, "📝 **Étape 3/7 :** Détails (lieu, etc.) ?")
        if not details_str: return
        
        # AJOUT ÉTAPE DURÉE
        duration_float = 2.0 # Défaut
        duration_str = await ask_text(user, "⏳ **Étape 4/7 :** Durée en heures ? (ex: `2.5` pour 2h30, `2` par défaut)")
        if duration_str:
            try:
                duration_float = float(duration_str.replace(',', '.'))
            except ValueError:
                await user.send("Durée invalide. Utilisation de 2.0 heures par défaut.")
                duration_float = 2.0
        else:
             await user.send("Utilisation de 2.0 heures par défaut.")
             
        recurrence_choice = await ask_choice(user, "🔁 **Étape 5/7 :** Récurrence ?", ["Aucune", "Hebdomadaire", "Mensuelle"])
        if not recurrence_choice: return
        recurrence_map = {"Aucune": "none", "Hebdomadaire": "weekly", "Mensuelle": "monthly"}
        recurrence_type = recurrence_map.get(recurrence_choice, "none")
        
        keep_choice = await ask_choice(user, "🧵 **Étape 6/7 :** Garder le fil après l'événement ?", ["Non (supprimer)", "Oui (archiver)"])
        if not keep_choice: return
        garder_le_fil = (keep_choice == "Oui (archiver)")
        
        target_group_str = await ask_text(user, "🔔 **Étape 7/7 (Optionnel) :** Rôle(s) ou Membre(s) à mentionner ? (ex: `@Membres @Louis`). 'aucun' si personne.")
        
        confirmation_msg = f"✅ **Terminé !** Création dans {original_channel.mention}."
        if target_group_str: confirmation_msg += f" Rappels pour {target_group_str}."
        await user.send(confirmation_msg)
        
        success = await create_event_post(
            date=date_str, time=time_str, details=details_str,
            recurrence_type=recurrence_type, target_group=target_group_str, 
            channel=original_channel, garder_le_fil=garder_le_fil,
            duration_hours=duration_float # AJOUT
        )
        
        if not success:
            await user.send(f"⚠️ **Échec de la publication !** Je n'ai pas pu poster l'événement dans {original_channel.mention}. Vérifiez les permissions du bot dans ce salon (voir logs).")
            
    except Exception as e:
        print(f"Erreur durant l'assistant : {e}")
        try:
            await user.send(f"Erreur lors de la création. Détails : {e}")
        except Exception:
            pass # L'utilisateur a peut-être bloqué le bot

# --- COMMANDE DE SUPPRESSION ---
# MODIFIÉ : Utilise asyncio.to_thread pour la BDD
def _db_admin_delete_sync(message_id):
    """Partie synchrone de la suppression admin (event + attendance)."""
    conn = sqlite3.connect(DB_NAME, timeout=DB_TIMEOUT)
    cursor = conn.cursor()
    cursor.execute("SELECT thread_id, channel_id FROM events WHERE message_id = ?", (message_id,))
    event_data = cursor.fetchone()
    
    if event_data:
        cursor.execute("DELETE FROM events WHERE message_id = ?", (message_id,))
        # CORRECTION FUITE DE DONNÉES (aussi appliquée ici)
        cursor.execute("DELETE FROM attendance WHERE message_id = ?", (message_id,))
        conn.commit()
        
    conn.close()
    return event_data # Retourne les données ou None

@bot.tree.command(name="supprimer_evenement", description="[ADMIN] Supprime manuellement un événement.")
@discord.app_commands.describe(message_id="L'ID du message de l'événement à supprimer")
@discord.app_commands.checks.has_permissions(administrator=True)
async def supprimer_evenement(interaction: discord.Interaction, message_id: str):
    await interaction.response.send_message(f"Recherche et suppression de {message_id}...", ephemeral=True)
    try: msg_id_int = int(message_id)
    except ValueError:
        await interaction.edit_original_response(content="Erreur : L'ID doit être un nombre."); return
    
    # MODIFIÉ : Opérations BDD dans un thread
    event_data = await asyncio.to_thread(_db_admin_delete_sync, msg_id_int)
    
    if not event_data:
        await interaction.edit_original_response(content="Événement non trouvé dans la BDD."); return
        
    thread_id, channel_id = event_data
    print(f"Suppression manuelle {msg_id_int} par {interaction.user.name}")
    
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        if channel: message = await channel.fetch_message(msg_id_int); await message.delete()
    except Exception: pass 
    
    try:
        thread = bot.get_channel(thread_id) or await bot.fetch_channel(thread_id)
        if thread: await thread.delete()
    except Exception: pass 
    
    await interaction.edit_original_response(content=f"Succès ! L'événement {msg_id_int} a été supprimé.")

# --- COMMANDE D'ANNULATION ---
# MODIFIÉ : Utilise asyncio.to_thread pour la BDD
def _db_admin_cancel_sync(message_id):
    """Partie synchrone de l'annulation admin."""
    conn = sqlite3.connect(DB_NAME, timeout=DB_TIMEOUT)
    cursor = conn.cursor()
    cursor.execute("SELECT thread_id, channel_id FROM events WHERE message_id = ?", (message_id,))
    event_data = cursor.fetchone()
    
    if event_data:
        cursor.execute("UPDATE events SET is_cancelled = 1 WHERE message_id = ?", (message_id,))
        conn.commit()
        
    conn.close()
    return event_data

@bot.tree.command(name="annuler_evenement", description="[ADMIN] Annule un événement (bloque les inscriptions).")
@discord.app_commands.describe(message_id="L'ID du message de l'événement à annuler")
@discord.app_commands.checks.has_permissions(administrator=True)
async def annuler_evenement(interaction: discord.Interaction, message_id: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try: msg_id_int = int(message_id)
    except ValueError:
        await interaction.followup.send("Erreur : L'ID doit être un nombre.", ephemeral=True); return
    
    # MODIFIÉ : Opérations BDD dans un thread
    event_data = await asyncio.to_thread(_db_admin_cancel_sync, msg_id_int)

    if not event_data:
        await interaction.followup.send(f"Événement non trouvé dans la BDD.", ephemeral=True); return
        
    thread_id, channel_id = event_data
    print(f"Événement {msg_id_int} annulé par {interaction.user.name}")
    
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        if channel:
            message = await channel.fetch_message(msg_id_int)
            original_embed = message.embeds[0]
            new_embed = original_embed.copy()
            new_embed.title = "🚫 ANNULÉ - " + original_embed.title
            new_embed.description = "**CET ÉVÉNEMENT EST OFFICIELLEMENT ANNULÉ.**\nLes inscriptions sont fermées.\n\n" + original_embed.description
            new_embed.color = discord.Color.red()
            new_embed.clear_fields()
            for field in original_embed.fields: new_embed.add_field(name=field.name, value=field.value, inline=field.inline)
            await message.edit(embed=new_embed, view=None) # Désactive les boutons
    except Exception as e: print(f"Erreur édition message (annulation): {e}")
    
    try:
        thread = bot.get_channel(thread_id) or await bot.fetch_channel(thread_id)
        if thread: await thread.send("🚫 **Cet événement a été annulé par un·e administrateur·rice.** Les inscriptions sont fermées.")
    except Exception as e: print(f"Erreur envoi message (annulation) fil {thread_id}: {e}")
    
    await interaction.followup.send(f"Succès ! L'événement {msg_id_int} a été marqué comme annulé.", ephemeral=True)

# --- GESTION DES ERREURS ---
@bot.event
async def on_tree_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message("Erreur : Permissions (Admin) insuffisantes.", ephemeral=True)
    else:
        print(f"Erreur commande non gérée : {error}")
        error_msg = f"Une erreur inattendue est survenue."
        try: 
            if interaction.response.is_done():
                await interaction.followup.send(error_msg, ephemeral=True)
            else:
                await interaction.response.send_message(error_msg, ephemeral=True)
        except Exception as e:
            print(f"Impossible d'envoyer un message d'erreur à l'utilisateur : {e}")


# ====================================================================
# 6. TÂCHES PLANIFIÉES (NETTOYAGE & RAPPELS)
# ====================================================================

# MODIFIÉ : Fonctions BDD pour les tâches
def _db_cleanup_get_events_sync():
    """Récupère tous les événements pour la tâche de nettoyage."""
    conn = sqlite3.connect(DB_NAME, timeout=DB_TIMEOUT)
    cursor = conn.cursor()
    # AJOUT : Récupère duration_hours
    cursor.execute("SELECT message_id, thread_id, event_date, event_time, details, target_group, channel_id, keep_thread, recurrence_type, duration_hours FROM events")
    all_events = cursor.fetchall()
    conn.close()
    return all_events

def _db_cleanup_delete_event_sync(message_id):
    """Supprime l'événement ET ses présences (Correction fuite BDD)."""
    conn = sqlite3.connect(DB_NAME, timeout=DB_TIMEOUT)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM events WHERE message_id = ?", (message_id,))
    # CORRECTION : Supprime aussi les présences associées
    cursor.execute("DELETE FROM attendance WHERE message_id = ?", (message_id,))
    conn.commit()
    conn.close()
    print(f"Nettoyage BDD : Événement {message_id} et présences supprimés.")

@tasks.loop(hours=1)
async def check_for_cleanup():
    print(f"{datetime.datetime.now()}: Tâche de nettoyage : Vérification...")
    
    # MODIFIÉ : Appel BDD dans un thread
    try:
        all_events = await asyncio.to_thread(_db_cleanup_get_events_sync)
    except Exception as e:
        print(f"Erreur BDD (check_for_cleanup): {e}")
        return
        
    if not all_events: return
        
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_local = datetime.datetime.now(FRENCH_TZ) # Pour la récurrence
    
    for event in all_events:
        try:
            message_id, thread_id, date, time, details, target_group, channel_id, keep_thread, recurrence_type, duration_hours = event
            
            if duration_hours is None: duration_hours = 2.0
            
            channel = bot.get_channel(channel_id)
            if not channel: 
                print(f"Nettoyage : Salon {channel_id} non trouvé, suppression BDD.")
                # Si le salon n'existe plus, on nettoie
                await asyncio.to_thread(_db_cleanup_delete_event_sync, message_id)
                continue 
                
            naive_dt = datetime.datetime.fromisoformat(f"{date}T{time}")
            event_start_local = naive_dt.replace(tzinfo=FRENCH_TZ)
            event_start_utc = event_start_local.astimezone(datetime.timezone.utc)
            
            # MODIFIÉ : Utilise duration_hours pour le temps de fin
            event_end_utc = event_start_utc + datetime.timedelta(hours=duration_hours)
            cleanup_time_utc = event_end_utc + datetime.timedelta(hours=24) # Nettoyage 24h APRÈS la FIN
            
            if now_utc > cleanup_time_utc:
                print(f"Nettoyage : Événement {message_id} terminé. Nettoyage...")
                next_local_dt = None
                
                # --- Récurrence (CORRIGÉ : Gestion du "rattrapage") ---
                if recurrence_type == 'weekly': 
                    next_local_dt = event_start_local + datetime.timedelta(weeks=1)
                    # CORRECTION : Boucle while pour rattraper les dates passées
                    while next_local_dt < now_local:
                        print(f"Rattrapage récurrence (Hebdo) {message_id}: {next_local_dt} est passé. Recalcul...")
                        next_local_dt = next_local_dt + datetime.timedelta(weeks=1)
                        
                elif recurrence_type == 'monthly': 
                    next_local_dt = event_start_local + relativedelta(months=1)
                    # CORRECTION : Boucle while pour rattraper les dates passées
                    while next_local_dt < now_local:
                        print(f"Rattrapage récurrence (Mensuel) {message_id}: {next_local_dt} est passé. Recalcul...")
                        next_local_dt = next_local_dt + relativedelta(months=1)
                        
                if next_local_dt:
                    # Si on a trouvé une date future valide
                    next_date_str = next_local_dt.strftime("%Y-%m-%d")
                    next_time_str = next_local_dt.strftime("%H:%M:%S")
                    
                    if not keep_thread:
                        print(f"Nettoyage : Purge anciens messages bot dans {channel.id}...")
                        def is_bot_message(m): return m.author == bot.user
                        try: await channel.purge(limit=100, check=is_bot_message, bulk=False)
                        except Exception as e: print(f"Erreur purge : {e}")
                        
                    print(f"Nettoyage : Création prochain événement récurrent ({recurrence_type})...")
                    # MODIFIÉ : Passe duration_hours au prochain événement
                    await create_event_post(next_date_str, next_time_str, details, recurrence_type, target_group, channel, bool(keep_thread), duration_hours)

                # --- Rapport Final ---
                summary = await get_attendance_summary(message_id) # Appel async BDD
                summary_embed = discord.Embed(title=f"✅ Rapport final {date}", description="Événement terminé.", color=discord.Color.dark_grey())
                coming_list = "\n".join([f"• {name}" for name, user_id in summary["coming"]]) or "Personne"
                maybe_list = "\n".join([f"• {name}" for name, user_id in summary["maybe"]]) or "Personne"
                not_coming_list = "\n".join([f"• {name}" for name, user_id in summary["not_coming"]]) or "Personne"
                summary_embed.add_field(name="✅ Présent·e·s", value=coming_list, inline=False)
                summary_embed.add_field(name="❓ Indécis·e·s", value=maybe_list, inline=False)
                summary_embed.add_field(name="❌ Absent·e·s", value=not_coming_list, inline=False)

                # --- Nettoyage Discord ---
                if keep_thread:
                    try:
                        thread = bot.get_channel(thread_id) or await bot.fetch_channel(thread_id)
                        await thread.send(embed=summary_embed); await thread.send("Événement terminé. Fil archivé.")
                    except Exception: pass
                    try:
                        message = await channel.fetch_message(message_id)
                        await message.edit(embed=summary_embed, view=None) 
                    except Exception: pass
                else:
                    try:
                        thread = bot.get_channel(thread_id) or await bot.fetch_channel(thread_id)
                        await thread.send(embed=summary_embed); await thread.send("Fil supprimé.")
                        await thread.delete()
                    except Exception: pass
                    try:
                        message = await channel.fetch_message(message_id)
                        await message.delete() 
                    except Exception: pass

                # --- Suppression BDD (MODIFIÉ : Appel BDD dans thread) ---
                # Utilise la nouvelle fonction qui nettoie les deux tables
                await asyncio.to_thread(_db_cleanup_delete_event_sync, message_id)
                
        except Exception as e:
            print(f"Erreur MAJEURE boucle nettoyage (event {message_id}): {e}") 

# MODIFIÉ : Fonctions BDD pour les tâches de rappel
def _db_reminders_get_events_sync():
    """Récupère les événements pour les rappels."""
    conn = sqlite3.connect(DB_NAME, timeout=DB_TIMEOUT)
    cursor = conn.cursor()
    # AJOUT : Récupère duration_hours
    cursor.execute("SELECT message_id, thread_id, event_date, event_time, details, target_group, channel_id, reminder_3d_sent, reminder_24h_sent, reminder_dm_sent, duration_hours FROM events WHERE is_cancelled = 0")
    all_events = cursor.fetchall()
    conn.close()
    return all_events

def _db_reminders_update_sent_sync(message_id, flag_name):
    """Marque un rappel comme envoyé (ex: 'reminder_3d_sent')."""
    conn = sqlite3.connect(DB_NAME, timeout=DB_TIMEOUT)
    cursor = conn.cursor()
    # Utilisation de f-string sécurisée car flag_name vient de notre propre code
    cursor.execute(f"UPDATE events SET {flag_name} = 1 WHERE message_id = ?", (message_id,))
    conn.commit()
    conn.close()

@tasks.loop(hours=1) 
async def check_reminders():
    print(f"{datetime.datetime.now()}: Tâche de rappel : Vérification...")
    
    # MODIFIÉ : Appel BDD dans un thread
    try:
        all_events = await asyncio.to_thread(_db_reminders_get_events_sync)
    except Exception as e:
        print(f"Erreur BDD (check_reminders): {e}")
        return
        
    if not all_events: return
        
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_local_date = datetime.datetime.now(FRENCH_TZ).date()
    
    for event in all_events:
        try:
            message_id, thread_id, event_date_str, event_time_str, details, target_group, channel_id, reminder_3d_sent, reminder_24h_sent, reminder_dm_sent, duration_hours = event
            
            channel = bot.get_channel(channel_id)
            if not channel: continue
                
            naive_dt = datetime.datetime.fromisoformat(f"{event_date_str}T{event_time_str}")
            event_start_local = naive_dt.replace(tzinfo=FRENCH_TZ)
            event_start_utc = event_start_local.astimezone(datetime.timezone.utc)
            time_until_event = event_start_utc - now_utc
            total_seconds = time_until_event.total_seconds()
            
            if total_seconds < 0: # Événement déjà commencé
                continue 
                
            # --- Rappel J-3 ---
            event_local_date = event_start_local.date()
            three_days_away = now_local_date + datetime.timedelta(days=3)
            if not reminder_3d_sent and event_local_date == three_days_away and target_group:
                print(f"Rappel : Envoi J-3 pour {message_id}...")
                day_of_week = calendar.day_name[event_local_date.weekday()]
                jours_fr = {"Monday": "lundi", "Tuesday": "mardi", "Wednesday": "mercredi", "Thursday": "jeudi", "Friday": "vendredi", "Saturday": "samedi", "Sunday": "dimanche"}
                jour_fr = jours_fr.get(day_of_week, day_of_week)
                reminder_message = (f"🔔 **Rappel !** Entraînement ce **{jour_fr}** ! {target_group} - confirmez votre présence. (Heure : {event_time_str} Paris)")
                await channel.send(reminder_message)
                
                # MODIFIÉ : Appel BDD dans un thread
                await asyncio.to_thread(_db_reminders_update_sent_sync, message_id, "reminder_3d_sent")

            # --- Rappel H-24 ---
            if not reminder_24h_sent and (23 * 3600 < total_seconds <= 24 * 3600):
                print(f"Rappel : Envoi H-24 pour {message_id}...")
                thread = bot.get_channel(thread_id) or await bot.fetch_channel(thread_id)
                if thread:
                    hours_remaining = int(total_seconds // 3600)
                    minutes_remaining = int((total_seconds % 3600) // 60)
                    temps_restant_str = f"{hours_remaining}h{minutes_remaining:02d}"
                    embed = discord.Embed(title="🔔 Rappel : J-1", description=f"L'entraînement commence dans environ **{temps_restant_str}** !", color=discord.Color.blue())
                    await thread.send(embed=embed)
                    
                    summary = await get_attendance_summary(message_id) # Appel async
                    all_users_to_ping = summary['coming'] + summary['maybe']
                    if all_users_to_ping:
                        mention_string = " ".join([f"<@{user_id}>" for name, user_id in all_users_to_ping])
                        await thread.send(f"Rappel pour les participant·e·s et indécis·e·s : {mention_string}")
                        
                    # MODIFIÉ : Appel BDD dans un thread
                    await asyncio.to_thread(_db_reminders_update_sent_sync, message_id, "reminder_24h_sent")

            # --- Rappel MP H-2 ---
            if not reminder_dm_sent and (1 * 3600 < total_seconds <= 2 * 3600):
                print(f"Rappel : Envoi des MPs H-2 pour {message_id}...")
                summary = await get_attendance_summary(message_id) # Appel async
                all_users_to_ping = summary['coming'] + summary['maybe']
                if not all_users_to_ping: print("Aucun participant à notifier en MP.")
                
                # MODIFIÉ : Passe duration_hours au lien Google
                google_link = create_google_calendar_link(event_date_str, event_time_str, details, duration_hours)
                link_text = f"**[Ajouter à Google Calendar]({google_link})**" if google_link else ""

                hours_remaining = int(total_seconds // 3600)
                minutes_remaining = int((total_seconds % 3600) // 60)
                temps_restant_str = f"{hours_remaining}h{minutes_remaining:02d}" if hours_remaining > 0 else f"{minutes_remaining} minute(s)"
                
                embed = discord.Embed(title="🔔 Rappel d'entraînement", description=f"L'entraînement commence dans **{temps_restant_str}** !", color=discord.Color.green())
                embed.add_field(name="Date", value=f"{event_date_str} à {event_time_str}", inline=False)
                embed.add_field(name="Détails", value=details, inline=False)
                
                users_notified_count = 0
                for name, user_id in all_users_to_ping:
                    try:
                        user = await bot.fetch_user(user_id)
                        await user.send(content=link_text, embed=embed)
                        users_notified_count += 1
                    except discord.Forbidden: print(f"Erreur MP : Impossible d'envoyer à {name} (MPs fermés).")
                    except Exception as e: print(f"Erreur MP : Erreur inconnue (user {user_id}): {e}")
                
                print(f"Rappel H-2 : {users_notified_count} membres notifiés en MP.")
                # MODIFIÉ : Appel BDD dans un thread
                await asyncio.to_thread(_db_reminders_update_sent_sync, message_id, "reminder_dm_sent")

        except Exception as e:
            print(f"Tâche de rappel : Erreur lors du traitement de l'événement {message_id}: {e}") 

@check_for_cleanup.before_loop
@check_reminders.before_loop
async def before_tasks():
    await bot.wait_until_ready()

# ====================================================================
# 7. LANCEMENT DU BOT
# ====================================================================
bot.run(BOT_TOKEN)