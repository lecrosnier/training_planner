import discord
from discord.ext import commands, tasks
import sqlite3
import datetime
import calendar
from zoneinfo import ZoneInfo 

# ====================================================================
# 1. CONFIGURATION ET INITIALISATION
# ====================================================================
FRENCH_TZ = ZoneInfo("Europe/Paris") 
intents = discord.Intents.default()
intents.members = True          
intents.message_content = True  
bot = commands.Bot(command_prefix="!", intents=intents)

BOT_TOKEN = "INSERT BOT TOKEN HERE" 


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
        event_date TEXT, event_time TEXT, details TEXT, is_recurrent INTEGER DEFAULT 0,
        target_group TEXT, reminder_3d_sent INTEGER DEFAULT 0, 
        reminder_24h_sent INTEGER DEFAULT 0, keep_thread INTEGER DEFAULT 0 
    )''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER, user_id INTEGER,
        user_name TEXT, status TEXT, UNIQUE(message_id, user_id)
    )''')
    try: cursor.execute("SELECT is_recurrent FROM events LIMIT 1")
    except sqlite3.OperationalError: cursor.execute("ALTER TABLE events ADD COLUMN is_recurrent INTEGER DEFAULT 0")
    try: cursor.execute("SELECT target_group FROM events LIMIT 1")
    except sqlite3.OperationalError: cursor.execute("ALTER TABLE events ADD COLUMN target_group TEXT")
    try: cursor.execute("SELECT channel_id FROM events LIMIT 1")
    except sqlite3.OperationalError: cursor.execute("ALTER TABLE events ADD COLUMN channel_id INTEGER")
    try: cursor.execute("SELECT reminder_3d_sent FROM events LIMIT 1")
    except sqlite3.OperationalError: cursor.execute("ALTER TABLE events ADD COLUMN reminder_3d_sent INTEGER DEFAULT 0")
    try: cursor.execute("SELECT reminder_24h_sent FROM events LIMIT 1")
    except sqlite3.OperationalError: cursor.execute("ALTER TABLE events ADD COLUMN reminder_24h_sent INTEGER DEFAULT 0")
    try: cursor.execute("SELECT keep_thread FROM events LIMIT 1")
    except sqlite3.OperationalError: cursor.execute("ALTER TABLE events ADD COLUMN keep_thread INTEGER DEFAULT 0")
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

def get_event_end_time_utc(message_id):
    """Récupère l'heure de FIN (début + 2h) de l'événement (UTC) depuis la BDD."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT event_date, event_time FROM events WHERE message_id = ?", (message_id,))
    row = cursor.fetchone()
    conn.close()
    if not row: return None 
    date, time = row
    try:
        naive_dt = datetime.datetime.fromisoformat(f"{date}T{time}")
        local_dt = naive_dt.replace(tzinfo=FRENCH_TZ) 
        event_start_utc = local_dt.astimezone(datetime.timezone.utc)
        # Rétabli : Marge de 2 heures pour les retards
        event_end_utc = event_start_utc + datetime.timedelta(hours=2) 
        return event_end_utc
    except Exception as e:
        print(f"Erreur d'analyse de l'heure de fin d'événement depuis la BDD : {e}")
        return None

init_db()

# ====================================================================
# 3. LOGIQUE DES BOUTONS (VIEWS)
# ====================================================================

class TrainingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    async def update_message(self, interaction: discord.Interaction):
        summary = get_attendance_summary(interaction.message.id)
        coming_list = "\n".join([f"• {name}" for name, user_id in summary["coming"]]) or "— Aucun pour l'instant —"
        maybe_list = "\n".join([f"• {name}" for name, user_id in summary["maybe"]]) or "— Aucun pour l'instant —"
        not_coming_list = "\n".join([f"• {name}" for name, user_id in summary["not_coming"]]) or "— Aucun pour l'instant —"
        original_embed = interaction.message.embeds[0]
        new_embed = discord.Embed(title=original_embed.title, description=original_embed.description, color=original_embed.color)
        for field in original_embed.fields:
            if (not field.name.startswith("✅ Présent(s)") and not field.name.startswith("❓ Ne sait pas") and not field.name.startswith("❌ Absent(s)")):
                 new_embed.add_field(name=field.name, value=field.value, inline=field.inline)
        new_embed.add_field(name=f"✅ Présent(s) ({len(summary['coming'])})", value=coming_list, inline=True)
        new_embed.add_field(name=f"❓ Ne sait pas ({len(summary['maybe'])})", value=maybe_list, inline=True)
        new_embed.add_field(name=f"❌ Absent(s) ({len(summary['not_coming'])})", value=not_coming_list, inline=True)
        await interaction.message.edit(embed=new_embed, view=self)
    async def invite_and_update(self, interaction: discord.Interaction, status: str, response_text: str):
        log_attendance(interaction.message.id, interaction.user.id, interaction.user.display_name, status)
        try:
            thread = interaction.message.thread
            if thread:
                if status in ["Coming", "Maybe"]:
                    await thread.add_user(interaction.user)
                    response_text += "\n✅ **Vous avez été ajouté au fil de discussion.**"
                elif status == "Not Coming":
                    await thread.remove_user(interaction.user)
                    response_text += "\n👋 **Vous avez été retiré du fil de discussion.**"
        except discord.Forbidden: pass
        except Exception as e: print(f"Erreur gestion accès thread : {e}")
        await interaction.response.send_message(response_text, ephemeral=True)
        await self.update_message(interaction)
    
    # Rétabli : Vérifie 'get_event_end_time_utc'
    @discord.ui.button(label="✅ Je viens", style=discord.ButtonStyle.green, custom_id="coming")
    async def coming_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        event_end_utc = get_event_end_time_utc(interaction.message.id)
        if not event_end_utc or datetime.datetime.now(datetime.timezone.utc) > event_end_utc:
            await interaction.response.send_message("Désolé, cet événement est déjà terminé.", ephemeral=True)
            return
        await self.invite_and_update(interaction, "Coming", "Vous êtes marqué comme 'Présent'. Rendez-vous là-bas !")
    @discord.ui.button(label="❓ Je ne sais pas", style=discord.ButtonStyle.blurple, custom_id="maybe")
    async def maybe_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        event_end_utc = get_event_end_time_utc(interaction.message.id)
        if not event_end_utc or datetime.datetime.now(datetime.timezone.utc) > event_end_utc:
            await interaction.response.send_message("Désolé, cet événement est déjà terminé.", ephemeral=True)
            return
        await self.invite_and_update(interaction, "Maybe", "Vous êtes marqué comme 'Ne sait pas'. Merci de mettre à jour si possible !")
    @discord.ui.button(label="❌ Je ne viens pas", style=discord.ButtonStyle.red, custom_id="not_coming")
    async def not_coming_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        event_end_utc = get_event_end_time_utc(interaction.message.id)
        if not event_end_utc or datetime.datetime.now(datetime.timezone.utc) > event_end_utc:
            await interaction.response.send_message("Désolé, cet événement est déjà terminé.", ephemeral=True)
            return
        await self.invite_and_update(interaction, "Not Coming", "Vous êtes marqué comme 'Absent'. Merci d'avoir prévenu.")

# ====================================================================
# 4. FONCTION PRINCIPALE DE CRÉATION D'ÉVÉNEMENT
# ====================================================================
async def create_event_post(date: str, time: str, details: str, is_recurrent: bool, target_group: str, channel: discord.TextChannel, garder_le_fil: bool):
    try:
        naive_dt = datetime.datetime.fromisoformat(f"{date}T{time}")
        local_dt = naive_dt.replace(tzinfo=FRENCH_TZ) 
        event_start_utc = local_dt.astimezone(datetime.timezone.utc)
    except ValueError:
        await channel.send("Erreur : Format de date ou d'heure invalide.", delete_after=10)
        return
    embed = discord.Embed(
        title=f"📅 Entraînement hebdomadaire : {date}",
        description=f"**Heure**: {time} (Heure de Paris)\n**Lieu/Détails**: {details}",
        color=discord.Color.blue()
    )
    recurrence_text = " (Événement récurrent)" if is_recurrent else ""
    embed.add_field(name=f"Veuillez répondre{recurrence_text}", value="Cliquez sur un bouton ci-dessous.", inline=False)
    embed.add_field(name="✅ Présent(s) (0)", value="— Aucun pour l'instant —", inline=True)
    embed.add_field(name="❓ Ne sait pas (0)", value="— Aucun pour l'instant —", inline=True)
    embed.add_field(name="❌ Absent(s) (0)", value="— Aucun pour l'instant —", inline=True)
    view = TrainingView()
    message = await channel.send(embed=embed, view=view)
    thread_name = f"💬 Discussion entraînement du {date}"
    thread = await message.create_thread(name=thread_name, auto_archive_duration=1440) 
    await thread.send(f"Utilisez ce fil pour discuter des détails de l'entraînement du {date}.")
    if target_group:
         await channel.send(f"Nouvel entraînement publié ! {target_group} veuillez répondre. ({date} @ {time} Heure de Paris)")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
    INSERT INTO events (message_id, thread_id, channel_id, event_date, event_time, details, is_recurrent, target_group, reminder_3d_sent, reminder_24h_sent, keep_thread)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
    ''', (message.id, thread.id, channel.id, date, time, details, int(is_recurrent), target_group, int(garder_le_fil)))
    conn.commit()
    conn.close()
    return message.id

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

@bot.tree.command(name="creer_entrainement", description="Créer un nouvel entraînement (Heure de Paris)")
@discord.app_commands.describe(
    date="Date de l'entraînement (ex: AAAA-MM-JJ)",
    time="Heure de l'entraînement (ex: 19:00:00)",
    details="Détails supplémentaires (lieu, focus, etc.)",
    recurrent="Mettre True si l'événement est récurrent (hebdomadaire)",
    target_group="Le(s) rôle(s) à notifier (ex: @Groupe1 @Groupe2)",
    garder_le_fil="Mettre True pour NE PAS supprimer le fil après l'événement"
)
async def create_training(interaction: discord.Interaction, date: str, time: str, details: str, recurrent: bool = False, target_group: str = None, garder_le_fil: bool = False):
    await interaction.response.send_message(f"Création de l'entraînement pour le {date}...", ephemeral=True)
    channel = interaction.channel
    await create_event_post(date, time, details, recurrent, target_group, channel, garder_le_fil)
    await interaction.edit_original_response(content="Entraînement publié avec succès !")

@bot.tree.command(name="supprimer_evenement", description="[ADMIN] Supprime manuellement un événement, son fil, et sa récurrence.")
@discord.app_commands.describe(
    message_id="L'ID (du message) de l'événement à supprimer"
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def supprimer_evenement(interaction: discord.Interaction, message_id: str):
    """Gère la suppression manuelle d'une occurrence d'événement."""
    
    await interaction.response.send_message(f"Recherche et suppression de l'événement {message_id}...", ephemeral=True)
    
    try:
        msg_id_int = int(message_id)
    except ValueError:
        await interaction.edit_original_response(content="Erreur : L'ID doit être un nombre (clic droit > Copier l'ID du message).")
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # 1. Trouver l'événement dans la BDD pour obtenir les IDs du fil et du salon
    cursor.execute("SELECT thread_id, channel_id FROM events WHERE message_id = ?", (msg_id_int,))
    event_data = cursor.fetchone()
    
    if not event_data:
        await interaction.edit_original_response(content=f"Événement non trouvé dans la base de données. Suppression BDD annulée.")
        # On tente quand même de supprimer le message sur Discord au cas où il existerait
        try:
            message = await interaction.channel.fetch_message(msg_id_int)
            await message.delete()
            await interaction.followup.send("Message Discord trouvé et supprimé (il n'était pas dans la BDD).", ephemeral=True)
        except Exception:
            pass # Le message n'existe nulle part
        conn.close()
        return

    thread_id, channel_id = event_data
    
    # 2. Supprimer les composants Discord
    print(f"Suppression manuelle de l'événement {msg_id_int} demandée par {interaction.user.name}")
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        if channel:
            message = await channel.fetch_message(msg_id_int)
            await message.delete()
            print(f"Message {msg_id_int} supprimé de Discord.")
    except discord.NotFound:
        print(f"Message {msg_id_int} déjà supprimé de Discord.")
    except Exception as e:
        print(f"Erreur lors de la suppression du message {msg_id_int}: {e}")

    try:
        thread = bot.get_channel(thread_id) or await bot.fetch_channel(thread_id)
        if thread:
            await thread.delete()
            print(f"Thread {thread_id} supprimé de Discord.")
    except discord.NotFound:
        print(f"Thread {thread_id} déjà supprimé de Discord.")
    except Exception as e:
        print(f"Erreur lors de la suppression du thread {thread_id}: {e}")

    # 3. Supprimer de la Base de Données
    cursor.execute("DELETE FROM events WHERE message_id = ?", (msg_id_int,))
    cursor.execute("DELETE FROM attendance WHERE message_id = ?", (msg_id_int,))
    conn.commit()
    conn.close()
    
    print(f"Événement {msg_id_int} supprimé de la BDD.")
    await interaction.edit_original_response(content=f"Succès ! L'événement {msg_id_int} a été entièrement supprimé.")

# Gestion des erreurs pour les commandes slash
@bot.event
async def on_tree_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message("Erreur : Vous n'avez pas les permissions (Administrateur) pour utiliser cette commande.", ephemeral=True)
    else:
        # Affiche les autres erreurs dans la console et à l'utilisateur
        print(f"Erreur de commande non gérée : {error}")
        try:
            await interaction.response.send_message(f"Une erreur inattendue est survenue. Le développeur a été notifié.", ephemeral=True)
        except discord.InteractionResponded:
            await interaction.followup.send(f"Une erreur inattendue est survenue. Le développeur a été notifié.", ephemeral=True)

# ====================================================================
# 6. TÂCHES PLANIFIÉES (NETTOYAGE & RAPPELS) -- VERSION PRODUCTION
# ====================================================================

# Rétabli : Boucle toutes les heures
@tasks.loop(hours=1)
async def check_for_cleanup():
    print(f"{datetime.datetime.now()}: Tâche de nettoyage : Vérification des anciens événements...")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT message_id, thread_id, event_date, event_time, details, is_recurrent, target_group, channel_id, keep_thread FROM events")
    all_events = cursor.fetchall()
    if not all_events:
        conn.close()
        return
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    for event in all_events:
        message_id, thread_id, date, time, details, is_recurrent, target_group, channel_id, keep_thread = event
        try:
            channel = bot.get_channel(channel_id)
            if not channel: continue 
            naive_dt = datetime.datetime.fromisoformat(f"{date}T{time}")
            event_start_local = naive_dt.replace(tzinfo=FRENCH_TZ) 
            event_start_utc = event_start_local.astimezone(datetime.timezone.utc)
            
            # Rétabli : Nettoyage 24 heures après le début
            cleanup_time_utc = event_start_utc + datetime.timedelta(hours=24) 
            
            if now_utc > cleanup_time_utc:
                print(f"Tâche de nettoyage : L'événement {message_id} est terminé. Nettoyage...")
                
                # --- 1. Gestion de la Récurrence ---
                if is_recurrent:
                    next_local_dt = event_start_local + datetime.timedelta(weeks=1)
                    now_local = datetime.datetime.now(FRENCH_TZ)
                    if next_local_dt < now_local:
                        print(f"Tâche de nettoyage : La prochaine occurrence ({next_local_dt.strftime('%Y-%m-%d')}) est dans le passé. Récurrence annulée.")
                    else:
                        next_date_str = next_local_dt.strftime("%Y-%m-%d")
                        next_time_str = next_local_dt.strftime("%H:%M:%S")
                        if not keep_thread:
                            print(f"Tâche de nettoyage : Purge des anciens messages du bot dans {channel.id}...")
                            def is_bot_message(m): return m.author == bot.user
                            try:
                                await channel.purge(limit=100, check=is_bot_message, bulk=False)
                            except discord.Forbidden: print(f"ERREUR : Permission 'Gérer les messages' manquante pour la purge.")
                            except Exception as e: print(f"Erreur purge : {e}")
                        print(f"Tâche de nettoyage : Création du prochain événement pour {next_date_str}...")
                        await create_event_post(next_date_str, next_time_str, details, True, target_group, channel, bool(keep_thread))

                # --- 2. Rapport final des présences ---
                summary = get_attendance_summary(message_id)
                summary_embed = discord.Embed(
                    title=f"✅ Rapport de présence final pour le {date}", 
                    description="Cet événement est terminé.", 
                    color=discord.Color.dark_grey() # Parenthèses corrigées
                )
                coming_list = "\n".join([f"• {name}" for name, user_id in summary["coming"]]) or "Personne n'a confirmé 'Présent'"
                maybe_list = "\n".join([f"• {name}" for name, user_id in summary["maybe"]]) or "Personne n'a sélectionné 'Ne sait pas'"
                not_coming_list = "\n".join([f"• {name}" for name, user_id in summary["not_coming"]]) or "Personne n'a confirmé 'Absent'"
                summary_embed.add_field(name="✅ Présents", value=coming_list, inline=False)
                summary_embed.add_field(name="❓ Ne sait pas", value=maybe_list, inline=False)
                summary_embed.add_field(name="❌ Absents", value=not_coming_list, inline=False)

                # --- 3. Nettoyage (conditionnel) ---
                if keep_thread:
                    try:
                        thread = bot.get_channel(thread_id) or await bot.fetch_channel(thread_id)
                        await thread.send(embed=summary_embed); await thread.send("Cet événement est terminé. Le fil est archivé (non supprimé).")
                    except Exception: pass
                    try:
                        message = await channel.fetch_message(message_id)
                        await message.edit(embed=summary_embed, view=None) 
                    except Exception: pass
                else:
                    try:
                        thread = bot.get_channel(thread_id) or await bot.fetch_channel(thread_id)
                        await thread.send(embed=summary_embed); await thread.send("Ce fil de discussion va maintenant être supprimé.")
                        await thread.delete()
                    except Exception: pass
                    try:
                        message = await channel.fetch_message(message_id)
                        await message.delete() 
                    except Exception: pass

                # --- 4. Suppression de l'événement de la BDD ---
                cursor.execute("DELETE FROM events WHERE message_id = ?", (message_id,))
                conn.commit()
                print(f"Tâche de nettoyage : Événement {message_id} retiré de la BDD.")
        except Exception as e:
            print(f"Erreur majeure dans la boucle de nettoyage (événement {message_id}): {e}") 
    conn.close()

# Rétabli : Boucle toutes les heures
@tasks.loop(hours=1) 
async def check_reminders():
    print(f"{datetime.datetime.now()}: Tâche de rappel : Vérification des rappels J-3 et H-24...")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT message_id, thread_id, event_date, event_time, target_group, channel_id, reminder_3d_sent, reminder_24h_sent FROM events")
    all_events = cursor.fetchall()
    if not all_events:
        conn.close()
        return
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_local_date = datetime.datetime.now(FRENCH_TZ).date()
    for event in all_events:
        message_id, thread_id, event_date_str, event_time_str, target_group, channel_id, reminder_3d_sent, reminder_24h_sent = event
        try:
            channel = bot.get_channel(channel_id)
            if not channel: continue
            naive_dt = datetime.datetime.fromisoformat(f"{event_date_str}T{event_time_str}")
            event_start_local = naive_dt.replace(tzinfo=FRENCH_TZ)
            event_start_utc = event_start_local.astimezone(datetime.timezone.utc)
            
            # --- 1. Logique J-3 ---
            event_local_date = event_start_local.date()
            three_days_away = now_local_date + datetime.timedelta(days=3)
            if not reminder_3d_sent and event_local_date == three_days_away and target_group:
                print(f"Tâche de rappel : Envoi du rappel J-3 pour l'événement {message_id}...")
                day_of_week = calendar.day_name[event_local_date.weekday()]
                jours_fr = {"Monday": "lundi", "Tuesday": "mardi", "Wednesday": "mercredi", "Thursday": "jeudi", "Friday": "vendredi", "Saturday": "samedi", "Sunday": "dimanche"}
                jour_fr = jours_fr.get(day_of_week, day_of_week)
                reminder_message = (
                    f"🔔 **Rappel !** L'entraînement est prévu ce **{jour_fr}** ! "
                    f"{target_group} - merci de confirmer votre présence sur le message principal. "
                    f"(Heure : {event_time_str} Heure de Paris)"
                )
                await channel.send(reminder_message)
                cursor.execute("UPDATE events SET reminder_3d_sent = 1 WHERE message_id = ?", (message_id,))
                conn.commit()

            # --- 2. Logique H-24 ---
            time_until_event = event_start_utc - now_utc
            total_seconds = time_until_event.total_seconds()

            # Rétabli : Se déclenche entre 23 et 24 heures AVANT
            if not reminder_24h_sent and (23 * 3600 < total_seconds <= 24 * 3600):
                print(f"Tâche de rappel : Envoi du rappel H-24 pour l'événement {message_id}...")
                thread = bot.get_channel(thread_id) or await bot.fetch_channel(thread_id)
                if not thread: continue
                
                embed = discord.Embed(
                    title="🔔 Rappel : J-1", 
                    description="L'entraînement commence dans environ 24 heures !", 
                    color=discord.Color.blue() # Parenthèses corrigées
                )
                await thread.send(embed=embed)
                
                summary = get_attendance_summary(message_id)
                all_users_to_ping = summary['coming'] + summary['maybe']
                if all_users_to_ping:
                    mention_string = " ".join([f"<@{user_id}>" for name, user_id in all_users_to_ping])
                    await thread.send(f"Rappel pour les participants et indécis : {mention_string}")
                cursor.execute("UPDATE events SET reminder_24h_sent = 1 WHERE message_id = ?", (message_id,))
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