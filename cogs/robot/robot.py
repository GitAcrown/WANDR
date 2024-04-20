import asyncio
import logging
import re
from typing import Iterable
import unidecode
from openai import AsyncOpenAI
from datetime import datetime, timedelta

import discord
import tiktoken
from discord import Interaction, app_commands
from discord.ext import commands

from common import dataio
from common.utils import fuzzy, pretty, interface

logger = logging.getLogger(f'WANDR.{__name__.split(".")[-1]}')
MAX_COMPLETION_TOKENS : int = 250

# UI ---------------------------------------------------------------------

class ContinueButtonView(discord.ui.View):
    """Ajoute un bouton pour continuer une complétion de message"""
    def __init__(self, *, timeout: float = 90.0, author: discord.Member | None = None):
        super().__init__(timeout=timeout)
        self.author = author
        self.value = None
        
    @discord.ui.button(label='Continuer', style=discord.ButtonStyle.gray)
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self.stop()
        
    async def on_timeout(self) -> None:
        self.value = False
        self.stop()
        
    async def interaction_check(self, interaction: Interaction[discord.Client]) -> bool:
        if self.author:
            if interaction.user.id == self.author.id:
                return True
            await interaction.response.send_message("Seul l'auteur du message peut continuer la complétion.", ephemeral=True, delete_after=10)
            return False
        return True
    
class CreateOrLoadView(discord.ui.View):
    """Ajoute un menu de sélection pour créer ou charger un chatbot"""
    def __init__(self, cog: 'Robot', *, timeout: float = 120.0, author: discord.Member | None = None, channel: discord.TextChannel | discord.Thread):
        super().__init__(timeout=timeout)
        self._cog = cog
        self.author = author
        self.channel = channel
        self.value : str = ''
        
        self._chatbots_options = [
            discord.SelectOption(label="Chatbot temporaire", value="create", emoji='⌛')
        ]
        self.fill_chatbots()
        self.chatbots_select.options = self._chatbots_options
        
    def fill_chatbots(self):
        chatbots = self._cog.get_presets(self.channel.guild)
        for chatbot in chatbots:
            self._chatbots_options.append(discord.SelectOption(label=chatbot['name'], value=f"load:{chatbot['id']}", emoji='🤖'))

    @discord.ui.select(placeholder="Sélectionnez une option", min_values=1, max_values=1)
    async def chatbots_select(self, interaction: Interaction, select: discord.ui.Select):
        if select.values[0] == 'create':
            self.value = 'create'
            modal = CreateChatbotModal(self._cog, self.channel)
            await interaction.response.send_modal(modal)
            await modal.wait()
        else:
            self.value = select.values[0]
            preset = re.match(r"load:(\d+)", select.values[0])
            if preset:
                chatbot = self._cog.get_preset(self.channel.guild, int(preset.group(1)))
                if chatbot:
                    self._cog.attach_chatbot(self.channel, chatbot)
                else:
                    return await interaction.response.send_message("**Erreur** · Le chatbot personnalisé n'existe pas.", ephemeral=True)
            else:
                return await interaction.response.send_message("**Erreur** · Chatbot inconnu.", ephemeral=True)
        self.stop()
        
    async def on_timeout(self) -> None:
        self.value = 'stop'
        self.stop()
        
    async def interaction_check(self, interaction: Interaction[discord.Client]) -> bool:
        if self.author:
            if interaction.user.id == self.author.id:
                return True
            await interaction.response.send_message("Seul l'auteur de la commande peut réaliser cette action.", ephemeral=True, delete_after=10)
            return False
        return True
    
class CreateChatbotModal(discord.ui.Modal):
    def __init__(self, cog: 'Robot', channel: discord.TextChannel | discord.Thread):
        super().__init__(title="Nouvelle session de chatbot", timeout=300.0)
        self._cog = cog
        self.channel = channel

        self.system_prompt = discord.ui.TextInput(label="Instructions d'initialisation", 
                                                  placeholder="Contexte et instructions du chatbot",
                                                  default="Tu es un chatbot amical et conversationnel qui répond à toutes les questions des utilisateurs. N'hésite pas à donner des opinions et à engager des conversations.",
                                                  min_length=10,
                                                  max_length=500,
                                                  style=discord.TextStyle.paragraph)
        self.temperature = discord.ui.TextInput(label="Température",
                                                placeholder="Niveau de créativité du chatbot (0.1 - 2.0)",
                                                default="0.8",
                                                min_length=1,
                                                max_length=4,
                                                style=discord.TextStyle.short)
        
        self.add_item(self.system_prompt)
        self.add_item(self.temperature)
        
    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer()
        if not isinstance(interaction.guild, discord.Guild):
            return
        if not isinstance(interaction.user, discord.Member):
            return
        try:
            temperature = float(self.temperature.value)
        except ValueError:
            return await interaction.followup.send("**Erreur** · La température doit être un nombre entre 0.1 et 2.0.", ephemeral=True)
        
        if temperature < 0.1 or temperature > 2.0:
            return await interaction.followup.send("**Erreur** · La température doit être un nombre entre 0.1 et 2.0.", ephemeral=True)
        
        system_prompt = self.system_prompt.value
        system_tokens = tiktoken.get_encoding('cl100k_base').encode(system_prompt)
        if len(system_tokens) > MAX_COMPLETION_TOKENS // 2:
            return await interaction.followup.send(f"**Instructions trop longues** · Les instructions ne doivent pas dépasser {MAX_COMPLETION_TOKENS // 2} tokens (soit environ {MAX_COMPLETION_TOKENS // 2 * 2} caractères).", ephemeral=True)
        
        self._cog.attach_chatbot(self.channel, BaseChatbot(self._cog, system_prompt, temperature))
        await interaction.followup.send("Le chatbot temporaire a été créé avec succès.", ephemeral=True)
        self.stop()
        
    async def on_timeout(self) -> None:
        self.stop()

# CHATBOTS ---------------------------------------------------------------------

class BaseChatbot:
    """Représente un chatbot de base exploitant GPT-3.5"""
    def __init__(self, cog: 'Robot', system_prompt: str, temperature: float = 0.8):
        self.__cog = cog
        self.system_prompt = system_prompt
        self.temperature = temperature
        
        self._messages = []
        
    def add_message(self, role: str, content: str, username: str, preset_id: int = 0):
        self._messages.append({
            'preset_id': preset_id,
            'timestamp': datetime.now().timestamp(),
            'role': role,
            'content': content,
            'username': username
        })
        
    def remove_message(self, index: int):
        del self._messages[index]
        
    def get_messages(self) -> list[dict]:
        """Renvoie tous les messages du chatbot"""
        return self._messages
    
    def _sanitize_messages(self, messages: list[dict]) -> Iterable[dict[str, str]]:
        """Nettoie les messages pour les rendre compatibles avec OpenAI"""
        sanitized = []
        for message in messages:
            if 'username' in message and message['role'] == 'user':
                sanitized.append({'role': message['role'], 'content': message['content'], 'name': message['username']})
            else:
                sanitized.append({'role': message['role'], 'content': message['content']})
        return sanitized
    
    def get_context(self, token_limit: int = 1000) -> Iterable[dict[str, str]]:
        """Renvoie le contexte du chatbot pour une limite de jetons donnée"""
        tokenizer = tiktoken.get_encoding('cl100k_base')
        system = [{'role': 'system', 'content': self.system_prompt}]
        if not self._messages:
            return system
        context = []
        context_size = len(tokenizer.encode(str(self.system_prompt)))
        for message in self._messages[::-1]:
            if message['role'] == 'system':
                continue
            if len(tokenizer.encode(message['content'])) + context_size > token_limit:
                break
            context.append(message)
            context_size += len(tokenizer.encode(message['content']))
        if context:
            context = system + context[::-1]
        else: # On ajoute que le dernier message (pour être certain que le prompt soit transmis)
            context = system + [self._messages[-1]]
        return self._sanitize_messages(context)
    
    # --- Génération de texte ---
    
    async def get_completion(self, prompt: str, username: str = 'user') -> dict[str, str] | None:
        """Génère une réponse à partir d'un prompt donné"""
        if username:
            username = ''.join([c for c in unidecode.unidecode(username) if c.isalnum() or c.isspace()]).rstrip()
        
        self.add_message('user', prompt, username)
        context = self.get_context()
        if not context:
            return None
        
        client = self.__cog.client
        try:
            completion = await client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=context, # type: ignore
                max_tokens=MAX_COMPLETION_TOKENS,
                temperature=self.temperature
            )
        except Exception as e:
            logger.error(f"Erreur OpenAI : {e}", exc_info=True)
            return None
        
        payload = {}
        response = completion.choices[0].message.content if completion.choices else None
        if response:
            self.add_message('assistant', response, 'assistant')
            payload['response'] = response
            payload['finish_reason'] = completion.choices[0].finish_reason
        else:
            return None
        if completion.usage:
            payload['usage'] = completion.usage.total_tokens
        return payload
    
    
class CustomChatbot(BaseChatbot):
    """Représente un chatbot personnalisé exploitant GPT-3.5"""
    def __init__(self, cog: 'Robot', guild: discord.Guild, preset_id: int):
        self.__cog = cog
        self.guild = guild
        self.preset_id = preset_id
        
        self.data = cog.data.get(guild).fetch("SELECT * FROM presets WHERE id = ?", (preset_id,))
        if not self.data:
            raise ValueError(f"Préréglage '#{preset_id}' introuvable pour '{guild}'")
        self.system_prompt = self.data['system_prompt']
        self.temperature = self.data['temperature']
        
        self.name = self.data['name']
        
        super().__init__(cog, self.system_prompt, self.temperature)
        
        self._messages = self.__load_messages()
        
    def __load_messages(self) -> list[dict]:
        self.cleanup_messages_before(datetime.now() - timedelta(days=7))
        r = self.__cog.data.get(self.guild).fetch_all("SELECT * FROM messages WHERE preset_id = ? ORDER BY timestamp ASC", (self.preset_id,))
        return r if r else []
        
    def add_message(self, role: str, content: str, username: str):
        self.__cog.data.get(self.guild).execute(
            "INSERT INTO messages (preset_id, timestamp, role, content, username) VALUES (?, ?, ?, ?, ?)",
            (self.preset_id, datetime.now().timestamp(), role, content, username)
        )
        super().add_message(role, content, username, self.preset_id)
        
    def remove_message(self, index: int):
        self.__cog.data.get(self.guild).execute(
            "DELETE FROM messages WHERE preset_id = ? AND timestamp = ?",
            (self.preset_id, self._messages[index]['timestamp'])
        )
        super().remove_message(index)
        
    def clear_messages(self):
        self.__cog.data.get(self.guild).execute(
            "DELETE FROM messages WHERE preset_id = ?",
            (self.preset_id,)
        )
        self._messages.clear()
    
    def cleanup_messages_before(self, date: datetime):
        self.__cog.data.get(self.guild).execute(
            "DELETE FROM messages WHERE preset_id = ? AND timestamp < ?",
            (self.preset_id, date.timestamp())
        )
    
    async def get_completion(self, prompt: str, username: str = 'user') -> dict[str, str] | None:
        return await super().get_completion(prompt, username)
    
    # --- Propriétés ---
    
    @property
    def author(self) -> discord.Member | None:
        return self.guild.get_member(self.data['author_id']) if self.data else None
    

class Robot(commands.Cog):
    """Implémentation de ChatGPT 3.5 dans Discord"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_instance(self)
        
        presets = dataio.TableDefault(
            """CREATE TABLE IF NOT EXISTS presets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                system_prompt TEXT,
                temperature REAL DEFAULT 0.8,
                author_id INTEGER
            )"""
        )
        messages = dataio.TableDefault(
            """CREATE TABLE IF NOT EXISTS messages (
                preset_id INTEGER,
                timestamp INTEGER,
                role TEXT,
                content TEXT,
                username TEXT,
                PRIMARY KEY (preset_id, timestamp),
                FOREIGN KEY (preset_id) REFERENCES presets(id)
            )"""
        )
        self.data.set_defaults(discord.Guild, presets, messages)
        
        user_tracking = dataio.TableDefault(
            """CREATE TABLE IF NOT EXISTS user_tracking (
                user_id INTEGER PRIMARY KEY,
                tokens_generated INTEGER DEFAULT 0,
                blocked BOOLEAN CHECK (blocked IN (0, 1)) DEFAULT 0
            )"""
        )
        self.data.set_defaults('global', user_tracking)

        self.client = AsyncOpenAI(
            api_key=self.bot.config['OPENAI_API_KEY'], # type: ignore
        )
        
        self.__sessions : dict[int, BaseChatbot] = {}
    
    # --- Gestion des presets ---
    
    def get_preset(self, guild: discord.Guild, preset_id: int) -> CustomChatbot | None:
        """Obtenir un chatbot personnalisé à partir de son ID de préréglage"""
        try:
            return CustomChatbot(self, guild, preset_id)
        except ValueError:
            return None
    
    def get_presets(self, guild: discord.Guild) -> list[dict]:
        """Obtenir tous les chatbots personnalisés d'une guilde"""
        return self.data.get(guild).fetch_all("SELECT * FROM presets")
    
    def get_presets_by_author(self, guild: discord.Guild, author: discord.Member) -> list[dict]:
        """Obtenir tous les chatbots personnalisés d'une guilde créés par un auteur donné"""
        return self.data.get(guild).fetch_all("SELECT * FROM presets WHERE author_id = ?", (author.id,))
    
    def create_preset(self, name: str, system_prompt: str, temperature: float, author: discord.Member):
        """Créer un nouveau chatbot personnalisé"""
        self.data.get(author.guild).execute(
            "INSERT INTO presets (name, system_prompt, temperature, author_id) VALUES (?, ?, ?, ?)",
            (name, system_prompt, temperature, author.id)
        )
        
    def delete_preset(self, guild: discord.Guild, preset_id: int):
        """Supprimer un chatbot personnalisé"""
        self.data.get(guild).execute(
            "DELETE FROM presets WHERE id = ?",
            (preset_id,)
        )
        self.data.get(guild).execute(
            "DELETE FROM messages WHERE preset_id = ?",
            (preset_id,)
        )
    
    # --- Gestion des sessions ---
    
    def get_session(self, channel: discord.TextChannel | discord.Thread) -> BaseChatbot | None:
        """Obtenir le chatbot actuellement en session dans un salon donné"""
        return self.__sessions.get(channel.id)
    
    def attach_chatbot(self, channel: discord.TextChannel | discord.Thread, chatbot: BaseChatbot):
        """Démarrer une session de chatbot dans un salon donné"""
        self.__sessions[channel.id] = chatbot
        
    def detach_chatbot(self, channel: discord.TextChannel | discord.Thread):
        """Terminer une session de chatbot dans un salon donné"""
        self.__sessions.pop(channel.id, None)
        
    # --- Tracking des utilisateurs ---
    
    def get_user_tracking(self, user: discord.User | discord.Member) -> dict | None:
        """Obtenir les informations de suivi d'un utilisateur"""
        return self.data.get('global').fetch("SELECT * FROM user_tracking WHERE user_id = ?", (user.id,))
    
    def set_user_tracking(self, user: discord.User | discord.Member, tokens_generated: int, blocked: bool):
        """Mettre à jour les informations de suivi d'un utilisateur"""
        self.data.get('global').execute(
            "INSERT OR REPLACE INTO user_tracking (user_id, tokens_generated, blocked) VALUES (?, ?, ?)",
            (user.id, tokens_generated, blocked)
        )
        
    def increment_user_tokens(self, user: discord.User | discord.Member, tokens: int):
        """Incrémenter le nombre de jetons générés par un utilisateur"""
        tracking = self.get_user_tracking(user)
        if tracking:
            self.set_user_tracking(user, tracking['tokens_generated'] + tokens, tracking['blocked'])
        else:
            self.set_user_tracking(user, tokens, False)
            
    def block_user(self, user: discord.User | discord.Member, blocked: bool):
        """Bloquer ou débloquer un utilisateur de l'utilisation de ChatGPT"""
        tracking = self.get_user_tracking(user)
        if tracking:
            self.set_user_tracking(user, tracking['tokens_generated'], blocked)
        else:
            self.set_user_tracking(user, 0, blocked)
            
    # --- Affichage ---
    
    def preview_chatbot(self, name: str, system_prompt: str, temperature: float) -> discord.Embed:
        """Génère un aperçu d'un chatbot personnalisé"""
        embed = discord.Embed(title=name, color=discord.Color.blurple())
        embed.add_field(name="Instructions", value=pretty.codeblock(system_prompt))
        if temperature > 1.4:
            embed.add_field(name="Température", value=pretty.codeblock(f"{temperature} ⚠"))
        else:
            embed.add_field(name="Température", value=pretty.codeblock(f"{temperature}"))
        return embed
            
    # --- Exploitation de ChatGPT ---
    
    async def handle_completion(self, prompt_message: discord.Message, *, _continue_completion: bool = False, override: bool = False) -> bool:
        """Gérer une demande de complétion de message"""
        botuser = self.bot.user
        if not botuser:
            return False
        
        if not isinstance(prompt_message.channel, discord.TextChannel | discord.Thread):
            return False
        if not isinstance(prompt_message.author, discord.Member):    
            return False    
        
        chatbot = self.get_session(prompt_message.channel)
        if not chatbot:
            return False
        name = chatbot.name if isinstance(chatbot, CustomChatbot) else 'ChatGPT'
        
        tracking = self.get_user_tracking(prompt_message.author)
        if tracking and tracking['blocked']:
            return False
        
        channel = prompt_message.channel
        content = prompt_message.content
        if not content:
            return False
        if content.startswith(';'): # On ignore les commandes
            return False
        
        completion = None
        if _continue_completion: # Si la complétion précédente n'est pas terminée ('finish_reason' != 'stop')
            content = 'Suite'
            async with channel.typing():
                completion = await chatbot.get_completion(content, prompt_message.author.name)
        elif botuser.mentioned_in(prompt_message) or override:
            async with channel.typing():
                completion = await chatbot.get_completion(content, prompt_message.author.name)
        
        if completion: # Si une réponse a été générée
            response = f"`{name} :` {completion['response']}"
            is_finished = completion['finish_reason'] == 'stop'
            usage = completion.get('usage')
            if usage:
                self.increment_user_tokens(prompt_message.author, int(usage))
            
            if is_finished:
                await prompt_message.reply(response, 
                                           mention_author=False, 
                                           suppress_embeds=True, 
                                           allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False, replied_user=True))
                return True # On indique que la complétion s'est terminée

            view = ContinueButtonView(timeout=90, author=prompt_message.author)
            resp = await prompt_message.reply(response, 
                                              mention_author=False, 
                                              view=view, 
                                              suppress_embeds=True, 
                                              allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False, replied_user=True))
            await view.wait()
            if view.value is True:
                await resp.edit(view=None)
                await self.handle_completion(resp, _continue_completion=True)
            else:
                await resp.edit(view=None)
            return True
        return False
    
    # === COMMANDES ===
    
    @app_commands.command(name='ask')
    @app_commands.guild_only()
    @app_commands.rename(replace='remplacer')
    async def ask(self, interaction: Interaction, prompt: str, replace: bool = False):
        """Parler avec ChatGPT en utilisant la session chargée (ou en créer une nouvelle)
        
        :param prompt: Message à envoyer au chatbot
        :param replace: Remplacer le chatbot actuel par un nouveau
        """
        if not isinstance(interaction.channel, discord.TextChannel | discord.Thread):
            return await interaction.response.send_message("Cette commande n'est disponible que dans les salons textuels.", ephemeral=True)
        if not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Cette commande n'est disponible que pour les membres d'un serveur.", ephemeral=True)
        
        # On vérifie si l'utilisateur est bloqué
        tracking = self.get_user_tracking(interaction.user)
        if tracking and tracking['blocked']:
            return await interaction.response.send_message("**Bloqué** · Vous avez été bloqué de l'utilisation de ChatGPT.", ephemeral=True)
        
        if replace:
            self.detach_chatbot(interaction.channel)
        
        # On regarde s'il y a un chatbot en session, sinon on envoie un modal pour créer un chatbot de base
        disp_embed = False
        await interaction.response.defer(ephemeral=True)
        if not self.get_session(interaction.channel):
            view = CreateOrLoadView(self, author=interaction.user, channel=interaction.channel)
            await interaction.followup.send("**Aucun chatbot actif** · Créer un **chatbot temporaire** ou en **charger un existant** ?", view=view)
            if not await view.wait():
                if view.value == 'stop':
                    return await interaction.delete_original_response()
                elif view.value == 'create':
                    await interaction.edit_original_response(content="**Chatbot temporaire créé** · Le chatbot va répondre à votre message dans un instant...", view=None)
                    disp_embed = True
                elif view.value.startswith('load:'):
                    await interaction.edit_original_response(content="**Chatbot chargé** · Le chatbot va répondre à votre message dans un instant...", view=None)
                else:
                    return await interaction.delete_original_response()
            
        chatbot = self.get_session(interaction.channel)
        if not chatbot:
            return await interaction.followup.send("**Erreur** · Impossible de charger le chatbot.", ephemeral=True)
        name = chatbot.name if isinstance(chatbot, CustomChatbot) else 'ChatGPT'
        
        completion = await chatbot.get_completion(prompt, interaction.user.name)
        if not completion:
            return await interaction.followup.send("**Erreur** · Impossible de générer une réponse.", ephemeral=True)
        
        response = completion['response']
        usage = completion.get('usage')
        if usage:
            self.increment_user_tokens(interaction.user, int(usage))
        
        embed = None
        if disp_embed:
            embed = discord.Embed(title="Chatbot temporaire", color=discord.Color.blurple())
            embed.add_field(name="Instructions", value=pretty.codeblock(chatbot.system_prompt))
            embed.add_field(name="Température", value=pretty.codeblock(f"{chatbot.temperature} ⚠" if chatbot.temperature > 1.4 else f"{chatbot.temperature}"))
            await interaction.followup.send(f"`{name} :` {response}", ephemeral=False, embed=embed)
        else:
            await interaction.followup.send(f"`{name} :` {response}", ephemeral=False)
        await asyncio.sleep(8)
        await interaction.delete_original_response()
        
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Répondre automatiquement aux messages des utilisateurs avec ChatGPT"""
        if message.author.bot:
            return
        if message.channel.id in self.__sessions:
            await self.handle_completion(message)
    
    chatbot_group = app_commands.Group(name='chatbot', description="Gestion des presets de chatbots personnalisés", guild_only=True)
    
    @chatbot_group.command(name='load')
    @app_commands.rename(preset_id='id_chatbot', reinit='réinitialiser')
    async def chatbot_load(self, interaction: Interaction, preset_id: int, reinit: bool = False):
        """Charger un chatbot personnalisé dans la session actuelle
        
        :param preset_id: ID unique du chatbot à charger
        :param reinit: Effacer la mémoire précédente du chatbot
        """
        if not isinstance(interaction.channel, discord.TextChannel | discord.Thread):
            return await interaction.response.send_message("Cette commande n'est disponible que dans les salons textuels.", ephemeral=True)
        if not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Cette commande n'est disponible que pour les membres d'un serveur.", ephemeral=True)
        
        chatbot = self.get_preset(interaction.channel.guild, preset_id)
        if not chatbot or not chatbot.data:
            return await interaction.response.send_message("**Chatbot introuvable** · Le chatbot personnalisé n'existe pas.", ephemeral=True)
        
        if reinit:
            chatbot.clear_messages()
            
        self.attach_chatbot(interaction.channel, chatbot)
        embed = self.preview_chatbot(chatbot.name, chatbot.system_prompt, chatbot.temperature)
        await interaction.response.send_message(f"Le chatbot personnalisé ***{chatbot.data['name']}*** a été chargé avec succès sur le salon en cours.", embed=embed)
    
    @chatbot_group.command(name='list')
    async def chatbot_list(self, interaction: Interaction):
        """Lister tous les chatbots personnalisés de la guilde"""
        if not isinstance(interaction.guild, discord.Guild):
            return await interaction.response.send_message("**Inaccessible** · Cette commande n'est disponible que sur les serveurs.", ephemeral=True)
        
        presets = self.get_presets(interaction.guild)
        if not presets:
            return await interaction.response.send_message("Aucun chatbot personnalisé n'a été créé sur ce serveur.", ephemeral=True)
        
        await interaction.response.defer()
        embeds = []
        for preset in presets:
            embed = discord.Embed(title=preset['name'], color=discord.Color.blurple())
            embed.add_field(name="Instructions", value=pretty.codeblock(preset['system_prompt']))
            embed.add_field(name="Température", value=pretty.codeblock(f"{preset['temperature']}"))
            embed.set_footer(text=f"Page {len(embeds) + 1}/{len(presets)} · ID: {preset['id']} · Créé par {preset['author_id']}")
            embeds.append(embed)
        
        view = interface.EmbedPaginatorMenu(embeds=embeds, users=[interaction.user], loop=True)
        await interaction.followup.send(embed=embeds[0], view=view)
    
    @chatbot_group.command(name='new')
    @app_commands.rename(name='nom', system_prompt='initialisation', temperature='température')
    async def chatbot_new(self, interaction: Interaction, name: str, system_prompt: str, temperature: app_commands.Range[float, 0.1, 2.0] = 0.8):
        """Créer un nouveau chatbot personnalisé
        
        :param name: Nom du chatbot
        :param system_prompt: Instructions d'initialisation du chatbot
        :param temperature: Niveau de créativité du chatbot (0.1 - 2.0)
        """
        if not isinstance(interaction.guild, discord.Guild) or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("**Inaccessible** · Cette commande n'est disponible que sur les serveurs.", ephemeral=True)

        presets = self.get_presets(interaction.guild)
        if len(presets) >= 20:
            return await interaction.response.send_message("**Limite atteinte** · Vous avez atteint la limite de 20 chatbots personnalisés.", ephemeral=True)
        if len(name) > 32:
            return await interaction.response.send_message("**Nom trop long** · Le nom du chatbot ne doit pas dépasser 32 caractères.", ephemeral=True)
        
        system_tokens = tiktoken.get_encoding('cl100k_base').encode(system_prompt)
        if len(system_tokens) > MAX_COMPLETION_TOKENS // 2:
            return await interaction.response.send_message(f"**Instructions trop longues** · Les instructions ne doivent pas dépasser {MAX_COMPLETION_TOKENS // 2} tokens (soit environ {MAX_COMPLETION_TOKENS // 2 * 2} caractères).", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        embed = self.preview_chatbot(name, system_prompt, temperature)
        confview = interface.ConfirmationView(users=[interaction.user])
        await interaction.followup.send("Voulez-vous créer ce chatbot personnalisé ?", embed=embed, view=confview) # On affiche un aperçu du chatbot
        await confview.wait()
        if not confview.value:
            await interaction.edit_original_response(content="Création annulée.")
            await asyncio.sleep(10)
            await interaction.delete_original_response()
        
        self.create_preset(name, system_prompt, temperature, interaction.user)
        await interaction.edit_original_response(content=f"Le chatbot personnalisé ***{name}*** a été créé avec succès.", view=None)
        
    @chatbot_group.command(name='edit')
    @app_commands.rename(preset_id='id_chatbot', system_prompt='initialisation', temperature='température')
    async def chatbot_edit(self, interaction: Interaction, preset_id: int, name: str | None = None, system_prompt: str | None = None, temperature: app_commands.Range[float, 0.1, 2.0] | None = None):
        """Modifier un chatbot personnalisé

        :param preset_id: ID unique du chatbot à modifier
        :param name: Nouveau nom du chatbot (facultatif)
        :param system_prompt: Nouvelles instructions d'initialisation (facultatif)
        :param temperature: Nouveau niveau de créativité du chatbot (0.1 - 2.0) (facultatif)
        """
        if not isinstance(interaction.guild, discord.Guild) or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("**Inaccessible** · Cette commande n'est disponible que sur les serveurs.", ephemeral=True)
        
        chatbot = self.get_preset(interaction.guild, preset_id)
        if not chatbot or not chatbot.data:
            return await interaction.response.send_message("**Chatbot introuvable** · Le chatbot personnalisé n'existe pas.", ephemeral=True)
        
        if not interaction.user.guild_permissions.administrator:
            if chatbot.author and interaction.user.id != chatbot.author.id:
                return await interaction.response.send_message("**Autorisation refusée** · Vous n'êtes pas l'auteur de ce chatbot ou administrateur du serveur.", ephemeral=True)
        
        if name:
            if len(name) > 32:
                return await interaction.response.send_message("**Nom trop long** · Le nom du chatbot ne doit pas dépasser 32 caractères.", ephemeral=True)
        if system_prompt:
            system_tokens = tiktoken.get_encoding('cl100k_base').encode(system_prompt)
            if len(system_tokens) > MAX_COMPLETION_TOKENS // 2:
                return await interaction.response.send_message(f"**Instructions trop longues** · Les instructions ne doivent pas dépasser {MAX_COMPLETION_TOKENS // 2} tokens (soit environ {MAX_COMPLETION_TOKENS // 2 * 2} caractères).", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        embed = self.preview_chatbot(name or chatbot.data['name'], system_prompt or chatbot.data['system_prompt'], temperature or chatbot.data['temperature'])
        confview = interface.ConfirmationView(users=[interaction.user])
        await interaction.followup.send("Confirmez-vous les modifications ?", embed=embed, view=confview)
        await confview.wait()
        if not confview.value:
            await interaction.edit_original_response(content="Modification annulée.")
            await asyncio.sleep(10)
            await interaction.delete_original_response()
        
        if name:
            self.data.get(interaction.guild).execute("UPDATE presets SET name = ? WHERE id = ?", (name, preset_id))
        if system_prompt:
            self.data.get(interaction.guild).execute("UPDATE presets SET system_prompt = ? WHERE id = ?", (system_prompt, preset_id))
        if temperature:
            self.data.get(interaction.guild).execute("UPDATE presets SET temperature = ? WHERE id = ?", (temperature, preset_id))
        await interaction.edit_original_response(content="Le chatbot personnalisé a été modifié avec succès.", view=None)
        
    @chatbot_group.command(name='delete')
    @app_commands.rename(preset_id='id_chatbot')
    async def chatbot_delete(self, interaction: Interaction, preset_id: int):
        """Supprimer un chatbot personnalisé
        
        :param preset_id: ID unique du chatbot à supprimer
        """
        if not isinstance(interaction.guild, discord.Guild) or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("**Inaccessible** · Cette commande n'est disponible que sur les serveurs.", ephemeral=True)
        
        chatbot = self.get_preset(interaction.guild, preset_id)
        if not chatbot or not chatbot.data:
            return await interaction.response.send_message("**Chatbot introuvable** · Le chatbot personnalisé n'existe pas.", ephemeral=True)
        
        if not interaction.user.guild_permissions.administrator:
            if chatbot.author and interaction.user.id != chatbot.author.id:
                return await interaction.response.send_message("**Autorisation refusée** · Vous n'êtes pas l'auteur de ce chatbot ou administrateur du serveur.", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        confview = interface.ConfirmationView(users=[interaction.user])
        await interaction.followup.send("Confirmez-vous la suppression de ce chatbot personnalisé ?", view=confview)
        await confview.wait()
        if not confview.value:
            await interaction.edit_original_response(content="Suppression annulée.")
            await asyncio.sleep(10)
            await interaction.delete_original_response()
        
        self.delete_preset(interaction.guild, preset_id)
        await interaction.edit_original_response(content=f"Le chatbot personnalisé ***{chatbot.data['name']}*** a été supprimé avec succès.", view=None)
                    
    @chatbot_load.autocomplete('preset_id')
    @chatbot_edit.autocomplete('preset_id')
    @chatbot_delete.autocomplete('preset_id')
    async def chatbot_id_autocomplete(self, interaction: discord.Interaction, current: str):
        if not isinstance(interaction.guild, discord.Guild):
            return []
        presets = self.get_presets(interaction.guild)
        r = fuzzy.finder(current, presets, key=lambda x: x['name'])
        return [app_commands.Choice(name=p['name'], value=p['id']) for p in r]
        
    blocklist_group = app_commands.Group(name='blocklist', description="Gestion des utilisateurs bloqués", guild_only=True, default_permissions=discord.Permissions(manage_messages=True))
    
    @blocklist_group.command(name='block')
    async def blocklist_block(self, interaction: Interaction, user: discord.Member):
        """Bloquer un utilisateur de l'utilisation de ChatGPT
        
        :param user: Utilisateur à bloquer
        """
        if not isinstance(interaction.guild, discord.Guild):
            return await interaction.response.send_message("**Inaccessible** · Cette commande n'est disponible que sur les serveurs.", ephemeral=True)
        
        track = self.get_user_tracking(user)
        if track and track['blocked']:
            return await interaction.response.send_message("**Déjà bloqué** · Cet utilisateur a déjà été bloqué.", ephemeral=True)
        
        self.block_user(user, True)
        await interaction.response.send_message(f"L'utilisateur ***{user}*** a été bloqué de l'utilisation de ChatGPT.", ephemeral=True)
        
    @blocklist_group.command(name='unblock')
    async def blocklist_unblock(self, interaction: Interaction, user: discord.Member):
        """Débloquer un utilisateur de l'utilisation de ChatGPT
        
        :param user: Utilisateur à débloquer
        """
        if not isinstance(interaction.guild, discord.Guild):
            return await interaction.response.send_message("**Inaccessible** · Cette commande n'est disponible que sur les serveurs.", ephemeral=True)
        
        track = self.get_user_tracking(user)
        if not track or not track['blocked']:
            return await interaction.response.send_message("**Déjà débloqué** · Cet utilisateur n'est pas bloqué.", ephemeral=True)
        
        self.block_user(user, False)
        await interaction.response.send_message(f"L'utilisateur ***{user}*** a été débloqué de l'utilisation de ChatGPT.", ephemeral=True)
        
    stats_group = app_commands.Group(name='stats', description="Statistiques d'utilisation de ChatGPT", guild_only=True)
    
    @stats_group.command(name='usertokens')
    async def stats_usertokens(self, interaction: Interaction, user: discord.Member):
        """Afficher le nombre de jetons générés par un utilisateur
        
        :param user: Utilisateur à consulter
        """
        if not isinstance(interaction.guild, discord.Guild):
            return await interaction.response.send_message("**Inaccessible** · Cette commande n'est disponible que sur les serveurs.", ephemeral=True)
        
        track = self.get_user_tracking(user)
        if not track:
            return await interaction.response.send_message("**Inconnu** · Cet utilisateur n'a pas généré de jetons.", ephemeral=True)
        
        # 0.0005$ pour 1k tokens en input
        # 0.0015$ pour 1k tokens en output
        # 0.002$ pour 1k tokens en total en moyenne (estimation haute)
        conv = 0.002 / 1000
        cost = track['tokens_generated'] * conv
        
        await interaction.response.send_message(f"L'utilisateur ***{user}*** a généré **{track['tokens_generated']} tokens**, soit au maximum **{cost:.4f}$** (estimation).", ephemeral=True)
        
    @stats_group.command(name='top')
    async def stats_top(self, interaction: Interaction, top: int = 10):
        """Afficher le top des utilisateurs ayant généré le plus de jetons
        
        :param top: Nombre d'utilisateurs à afficher (par défaut : 10)
        """
        if not isinstance(interaction.guild, discord.Guild):
            return await interaction.response.send_message("**Inaccessible** · Cette commande n'est disponible que sur les serveurs.", ephemeral=True)
        
        users = self.data.get('global').fetch_all("SELECT * FROM user_tracking WHERE tokens_generated > 0 ORDER BY tokens_generated DESC LIMIT ?", (top,))
        if not users:
            return await interaction.response.send_message("Aucun utilisateur n'a généré de jetons.", ephemeral=True)
        users = [user for user in users if interaction.guild.get_member(user['user_id'])]
        
        embed = discord.Embed(title="Statistiques ChatGPT · Top utilisateurs", color=discord.Color.blurple())
        text = []
        for i, user in enumerate(users, start=1):
            member = interaction.guild.get_member(user['user_id'])
            if not member:
                continue
            text.append(f"{i}. {member.name} · {user['tokens_generated']}")
        
        embed.description = pretty.codeblock('\n'.join(text))
        embed.set_footer(text=f"Nombre de tokens générés par utilisateur\nTotal : {sum(u['tokens_generated'] for u in users)}")
        await interaction.response.send_message(embed=embed)
            
async def setup(bot):
    await bot.add_cog(Robot(bot))
