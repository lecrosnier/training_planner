import discord
from discord.ext import commands, tasks
import sqlite3
import datetime
import calendar
from zoneinfo import ZoneInfo 
import asyncio 
from dateutil.relativedelta import relativedelta 
import urllib.parse 

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

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS events (
        message_id INTEGER PRIMARY KEY, thread_id INTEGER, channel_id INTEGER, 
        event_date TEXT, event_time TEXT, details TEXT,
        is_recurrent INTEGER DEFAULT 0, 
        target_group TEXT, reminder_3d_sent INTEGER DEFAULT 0, 
        reminder_24h_sent INTEGER DEFAULT 0, keep_thread INTEGER DEFAULT 0,
        recurrence_type TEXT DEFAULT 'none', is_cancelled INTEGER DEFAULT 0,
        reminder_dm_sent INTEGER DEFAULT 0 
    )''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER, user_id INTEGER,
        user_name TEXT, status TEXT, UNIQUE(message_id, user_id)
    )''')
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
    conn.commit()
    conn.close()

def log_attendance(message_id, user_id, user_name, status):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
    REPLACE INTO attendance (message_id, user_id, user_name, status)
    VALUES (?, ?, ?, ?)
    ''', (message_id, user_id, user_name, status))
    conn.commit()
    conn.close()

def get_attendance_summary(message_id):
    conn = sqlite3.connect(DB_NAME)
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

def get_event_state(message_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT event_date, event_time, is_cancelled FROM events WHERE message_id = ?", (message_id,))
    row = cursor.fetchone()
    conn.close()
    if not row: return (None, False)
    date, time, is_cancelled = row
    try:
        naive_dt = datetime.datetime.fromisoformat(f"{date}T{time}")
        local_dt = naive_dt.replace(tzinfo=FRENCH_TZ) 
        event_start_utc = local_dt.astimezone(datetime.timezone.utc)
        event_end_utc = event_start_utc + datetime.timedelta(hours=2) # Marge de 2h
        return (event_end_utc, bool(is_cancelled))
    except Exception as e:
        print(f"Erreur d'analyse BDD (get_event_state) : {e}")
        return (None, False)

def create_google_calendar_link(event_date, event_time, details):
    try:
        naive_dt = datetime.datetime.fromisoformat(f"{event_date}T{event_time}")
        start_local = naive_dt.replace(tzinfo=FRENCH_TZ)
        end_local = start_local + datetime.timedelta(hours=2)
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

init_db()

# ====================================================================
# 3. LOGIQUE DES BOUTONS (VIEWS) -- TEXTE INCLUSIF
# ====================================================================
class TrainingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    async def update_message(self, interaction: discord.Interaction):
        summary = get_attendance_summary(interaction.message.id)
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
        log_attendance(interaction.message.id, interaction.user.id, interaction.user.display_name, status)
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
        event_end_utc, is_cancelled = get_event_state(interaction.message.id)
        if is_cancelled:
            await interaction.response.send_message("Désolé, cet événement a été **annulé**. Les inscriptions sont fermées.", ephemeral=True)
            return
        if not event_end_utc or datetime.datetime.now(datetime.timezone.utc) > event_end_utc:
            await interaction.response.send_message("Désolé, cet événement est déjà terminé.", ephemeral=True)
            return
        await self.invite_and_update(interaction, "Coming", "Vous êtes marqué·e comme 'Présent·e'. Rendez-vous là-bas !")

    @discord.ui.button(label="❓ Je ne sais pas", style=discord.ButtonStyle.blurple, custom_id="maybe")
    async def maybe_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        event_end_utc, is_cancelled = get_event_state(interaction.message.id)
        if is_cancelled:
            await interaction.response.send_message("Désolé, cet événement a été **annulé**. Les inscriptions sont fermées.", ephemeral=True)
            return
        if not event_end_utc or datetime.datetime.now(datetime.timezone.utc) > event_end_utc:
            await interaction.response.send_message("Désolé, cet événement est déjà terminé.", ephemeral=True)
            return
        await self.invite_and_update(interaction, "Maybe", "Vous êtes marqué·e comme 'Indécis·e'. Merci de mettre à jour si possible !")

    @discord.ui.button(label="❌ Je ne viens pas", style=discord.ButtonStyle.red, custom_id="not_coming")
    async def not_coming_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        event_end_utc, is_cancelled = get_event_state(interaction.message.id)
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
async def create_event_post(date: str, time: str, details: str, recurrence_type: str, target_group: str, channel: discord.TextChannel, garder_le_fil: bool):
    try:
        naive_dt = datetime.datetime.fromisoformat(f"{date}T{time}")
        local_dt = naive_dt.replace(tzinfo=FRENCH_TZ) 
    except ValueError:
        await channel.send("Erreur : Format de date ou d'heure invalide.", delete_after=10)
        return False
        
    embed = discord.Embed(title=f"📅 Entraînement : {date}", description=f"**Heure**: {time} (Heure de Paris)\n**Lieu/Détails**: {details}", color=discord.Color.blue())
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

    # --- CORRIGÉ : Envoi du 'target_group' brut ---
    if target_group:
        # On envoie la chaîne brute. Discord résoudra les mentions valides (rôles + utilisateurs).
        await channel.send(f"Nouvel entraînement publié ! {target_group} veuillez répondre. ({date} @ {time} Heure de Paris)")

    # Enregistrement BDD
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    is_recurrent_int = 1 if recurrence_type != 'none' else 0
    thread_id_to_save = thread.id if thread else None 
    cursor.execute('''
    INSERT INTO events (message_id, thread_id, channel_id, event_date, event_time, details, is_recurrent, target_group, reminder_3d_sent, reminder_24h_sent, keep_thread, recurrence_type, is_cancelled)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, 0)
    ''', (message.id, thread_id_to_save, channel.id, date, time, details, is_recurrent_int, target_group, int(garder_le_fil), recurrence_type))
    conn.commit()
    conn.close()
    return True

# ====================================================================
# 5. ÉVÉNEMENTS DU BOT ET COMMANDES
# ====================================================================
@bot.event
async def on_ready():
    print(f'Connecté en tant que {bot.user}')
    print('Le bot est prêt !')
    bot.add_view(TrainingView()) 
    await bot.tree.sync() 
    check_for_cleanup.start()
    check_reminders.start()

# --- COMMANDE SLASH (RAPIDE) ---
@bot.tree.command(name="creer_entrainement", description="Créer un nouvel entraînement (Heure de Paris)")
@discord.app_commands.describe(
    date="Date (AAAA-MM-JJ)", time="Heure (HH:MM:SS)", details="Détails", 
    recurrent="[Obsolète] True=Hebdo", target_group="Rôle(s) ou Membre(s) à notifier", 
    garder_le_fil="True=NE PAS supprimer le fil"
)
async def create_training(interaction: discord.Interaction, date: str, time: str, details: str, recurrent: bool = False, target_group: str = None, garder_le_fil: bool = False):
    await interaction.response.send_message(f"Création de l'entraînement...", ephemeral=True)
    channel = interaction.channel
    recurrence_str = 'weekly' if recurrent else 'none'
    
    success = await create_event_post(date, time, details, recurrence_str, target_group, channel, garder_le_fil)
    
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
    for choice in choices:
        button = discord.ui.Button(label=choice, style=discord.ButtonStyle.primary)
        async def callback(interaction: discord.Interaction, button_label: str):
            await interaction.response.edit_message(content=f"Sélectionné·e : **{button_label}**", view=None)
            result.set_result(button_label)
        button.callback = lambda i, b=choice: callback(i, b)
        view.add_item(button)
    await dm.send(question, view=view)
    try: return await result
    except asyncio.TimeoutError:
        await dm.send("Délai expiré. Relancez la commande."); return None

@bot.tree.command(name="creer_wizard", description="[ADMIN] Lancer l'assistant de création d'événement en MP.")
@discord.app_commands.checks.has_permissions(administrator=True)
async def creer_wizard(interaction: discord.Interaction):
    user = interaction.user
    original_channel = interaction.channel 
    await interaction.response.send_message(f"Parfait ! Message privé envoyé.", ephemeral=True)
    try:
        date_str = await ask_text(user, "📅 **Étape 1/6 :** Date ? (AAAA-MM-JJ)")
        if not date_str: return
        time_str = await ask_text(user, "🕒 **Étape 2/6 :** Heure de début ? (HH:MM:SS)")
        if not time_str: return
        details_str = await ask_text(user, "📝 **Étape 3/6 :** Détails (lieu, etc.) ?")
        if not details_str: return
        recurrence_choice = await ask_choice(user, "🔁 **Étape 4/6 :** Récurrence ?", ["Aucune", "Hebdomadaire", "Mensuelle"])
        if not recurrence_choice: return
        recurrence_map = {"Aucune": "none", "Hebdomadaire": "weekly", "Mensuelle": "monthly"}
        recurrence_type = recurrence_map.get(recurrence_choice, "none")
        keep_choice = await ask_choice(user, "🧵 **Étape 5/6 :** Garder le fil après l'événement ?", ["Non (supprimer)", "Oui (archiver)"])
        if not keep_choice: return
        garder_le_fil = (keep_choice == "Oui (archiver)")
        target_group_str = await ask_text(user, "🔔 **Étape 6/6 (Optionnel) :** Rôle(s) ou Membre(s) à mentionner ? (ex: `@Membres @Louis`). 'aucun' si personne.")
        
        confirmation_msg = f"✅ **Terminé !** Création dans {original_channel.mention}."
        if target_group_str: confirmation_msg += f" Rappels pour {target_group_str}."
        await user.send(confirmation_msg)
        
        success = await create_event_post(
            date=date_str, time=time_str, details=details_str,
            recurrence_type=recurrence_type, target_group=target_group_str, 
            channel=original_channel, garder_le_fil=garder_le_fil
        )
        
        if not success:
            await user.send(f"⚠️ **Échec de la publication !** Je n'ai pas pu poster l'événement dans {original_channel.mention}. Vérifiez les permissions du bot dans ce salon (voir logs).")
            
    except Exception as e:
        print(f"Erreur durant l'assistant : {e}")
        await user.send(f"Erreur lors de la création. Détails : {e}")

# --- COMMANDE DE SUPPRESSION ---
@bot.tree.command(name="supprimer_evenement", description="[ADMIN] Supprime manuellement un événement.")
@discord.app_commands.describe(message_id="L'ID du message de l'événement à supprimer")
@discord.app_commands.checks.has_permissions(administrator=True)
async def supprimer_evenement(interaction: discord.Interaction, message_id: str):
    await interaction.response.send_message(f"Recherche et suppression de {message_id}...", ephemeral=True)
    try: msg_id_int = int(message_id)
    except ValueError:
        await interaction.edit_original_response(content="Erreur : L'ID doit être un nombre."); return
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT thread_id, channel_id FROM events WHERE message_id = ?", (msg_id_int,))
    event_data = cursor.fetchone()
    if not event_data:
        await interaction.edit_original_response(content="Événement non trouvé dans la BDD."); conn.close(); return
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
    cursor.execute("DELETE FROM events WHERE message_id = ?", (msg_id_int,))
    cursor.execute("DELETE FROM attendance WHERE message_id = ?", (msg_id_int,))
    conn.commit()
    conn.close()
    await interaction.edit_original_response(content=f"Succès ! L'événement {msg_id_int} a été supprimé.")

# --- COMMANDE D'ANNULATION ---
@bot.tree.command(name="annuler_evenement", description="[ADMIN] Annule un événement (bloque les inscriptions).")
@discord.app_commands.describe(message_id="L'ID du message de l'événement à annuler")
@discord.app_commands.checks.has_permissions(administrator=True)
async def annuler_evenement(interaction: discord.Interaction, message_id: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try: msg_id_int = int(message_id)
    except ValueError:
        await interaction.followup.send("Erreur : L'ID doit être un nombre.", ephemeral=True); return
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT thread_id, channel_id FROM events WHERE message_id = ?", (msg_id_int,))
    event_data = cursor.fetchone()
    if not event_data:
        await interaction.followup.send(f"Événement non trouvé dans la BDD.", ephemeral=True); conn.close(); return
    thread_id, channel_id = event_data
    cursor.execute("UPDATE events SET is_cancelled = 1 WHERE message_id = ?", (msg_id_int,))
    conn.commit()
    conn.close()
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
            await message.edit(embed=new_embed, view=message.view)
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
        try: await interaction.response.send_message(error_msg, ephemeral=True)
        except discord.InteractionResponded: await interaction.followup.send(error_msg, ephemeral=True)

# ====================================================================
# 6. TÂCHES PLANIFIÉES (NETTOYAGE & RAPPELS) -- VERSION PRODUCTION
# ====================================================================

@tasks.loop(hours=1)
async def check_for_cleanup():
    print(f"{datetime.datetime.now()}: Tâche de nettoyage : Vérification...")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT message_id, thread_id, event_date, event_time, details, target_group, channel_id, keep_thread, recurrence_type FROM events")
    all_events = cursor.fetchall()
    if not all_events: conn.close(); return
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    for event in all_events:
        message_id, thread_id, date, time, details, target_group, channel_id, keep_thread, recurrence_type = event
        try:
            channel = bot.get_channel(channel_id)
            if not channel: continue 
            naive_dt = datetime.datetime.fromisoformat(f"{date}T{time}")
            event_start_local = naive_dt.replace(tzinfo=FRENCH_TZ) 
            event_start_utc = event_start_local.astimezone(datetime.timezone.utc)
            cleanup_time_utc = event_start_utc + datetime.timedelta(hours=24) 
            
            if now_utc > cleanup_time_utc:
                print(f"Nettoyage : Événement {message_id} terminé. Nettoyage...")
                next_local_dt = None
                
                # --- Récurrence ---
                if recurrence_type == 'weekly': next_local_dt = event_start_local + datetime.timedelta(weeks=1)
                elif recurrence_type == 'monthly': next_local_dt = event_start_local + relativedelta(months=1)
                
                if next_local_dt:
                    now_local = datetime.datetime.now(FRENCH_TZ)
                    if next_local_dt < now_local:
                        print(f"Nettoyage : Prochaine occurrence passée. Récurrence annulée.")
                    else:
                        next_date_str = next_local_dt.strftime("%Y-%m-%d")
                        next_time_str = next_local_dt.strftime("%H:%M:%S")
                        if not keep_thread:
                            print(f"Nettoyage : Purge anciens messages bot dans {channel.id}...")
                            def is_bot_message(m): return m.author == bot.user
                            try: await channel.purge(limit=100, check=is_bot_message, bulk=False)
                            except Exception as e: print(f"Erreur purge : {e}")
                        print(f"Nettoyage : Création prochain événement récurrent ({recurrence_type})...")
                        await create_event_post(next_date_str, next_time_str, details, recurrence_type, target_group, channel, bool(keep_thread))

                # --- Rapport Final ---
                summary = get_attendance_summary(message_id)
                summary_embed = discord.Embed(title=f"✅ Rapport final {date}", description="Événement terminé.", color=discord.Color.dark_grey())
                coming_list = "\n".join([f"• {name}" for name, user_id in summary["coming"]]) or "Personne"
                maybe_list = "\n".join([f"• {name}" for name, user_id in summary["maybe"]]) or "Personne"
                not_coming_list = "\n".join([f"• {name}" for name, user_id in summary["not_coming"]]) or "Personne"
                summary_embed.add_field(name="✅ Présent·e·s", value=coming_list, inline=False)
                summary_embed.add_field(name="❓ Indécis·e·s", value=maybe_list, inline=False)
                summary_embed.add_field(name="❌ Absent·e·s", value=not_coming_list, inline=False)

                # --- Nettoyage ---
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

                # --- Suppression BDD ---
                cursor.execute("DELETE FROM events WHERE message_id = ?", (message_id,))
                conn.commit()
                print(f"Nettoyage : Événement {message_id} retiré BDD.")
        except Exception as e:
            print(f"Erreur MAJEURE boucle nettoyage (event {message_id}): {e}") 
    conn.close()

@tasks.loop(hours=1) 
async def check_reminders():
    print(f"{datetime.datetime.now()}: Tâche de rappel : Vérification...")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT message_id, thread_id, event_date, event_time, details, target_group, channel_id, reminder_3d_sent, reminder_24h_sent, reminder_dm_sent FROM events WHERE is_cancelled = 0")
    all_events = cursor.fetchall()
    if not all_events: conn.close(); return
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_local_date = datetime.datetime.now(FRENCH_TZ).date()
    for event in all_events:
        message_id, thread_id, event_date_str, event_time_str, details, target_group, channel_id, reminder_3d_sent, reminder_24h_sent, reminder_dm_sent = event
        try:
            channel = bot.get_channel(channel_id)
            if not channel: continue
            naive_dt = datetime.datetime.fromisoformat(f"{event_date_str}T{event_time_str}")
            event_start_local = naive_dt.replace(tzinfo=FRENCH_TZ)
            event_start_utc = event_start_local.astimezone(datetime.timezone.utc)
            time_until_event = event_start_utc - now_utc
            total_seconds = time_until_event.total_seconds()
            
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
                cursor.execute("UPDATE events SET reminder_3d_sent = 1 WHERE message_id = ?", (message_id,))
                conn.commit()

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
                    summary = get_attendance_summary(message_id)
                    all_users_to_ping = summary['coming'] + summary['maybe']
                    if all_users_to_ping:
                        mention_string = " ".join([f"<@{user_id}>" for name, user_id in all_users_to_ping])
                        await thread.send(f"Rappel pour les participant·e·s et indécis·e·s : {mention_string}")
                    cursor.execute("UPDATE events SET reminder_24h_sent = 1 WHERE message_id = ?", (message_id,))
                    conn.commit()

            # --- Rappel MP H-2 ---
            if not reminder_dm_sent and (1 * 3600 < total_seconds <= 2 * 3600):
                print(f"Rappel : Envoi des MPs H-2 pour {message_id}...")
                summary = get_attendance_summary(message_id)
                all_users_to_ping = summary['coming'] + summary['maybe']
                if not all_users_to_ping: print("Aucun participant à notifier en MP.")
                
                google_link = create_google_calendar_link(event_date_str, event_time_str, details)
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
                cursor.execute("UPDATE events SET reminder_dm_sent = 1 WHERE message_id = ?", (message_id,))
                conn.commit()

        except Exception as e:
            print(f"Tâche de rappel : Erreur lors du traitement de l'événement {message_id}: {e}") 
    conn.close() 

@check_for_cleanup.before_loop
@check_reminders.before_loop
async def before_tasks():
    await bot.wait_until_ready()

# ====================================================================
# 7. LANCEMENT DU BOT
# ====================================================================
bot.run(BOT_TOKEN)