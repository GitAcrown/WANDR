import logging
import random
import re
from datetime import datetime

import discord
from discord import Interaction, app_commands
from discord.ext import commands
from numpy import delete

from common import dataio, rankio
from common.utils import fuzzy, pretty, interface

logger = logging.getLogger(f'WANDR.{__name__.split(".")[-1]}')

# MENUS =======================================================

class FortuneCookieBaseView(discord.ui.View):
    """Vue de base pour l'ouverture de fortune cookies."""
    def __init__(self, cog: 'Messages', cookie_data: dict, opener: discord.Member):
        super().__init__(timeout=20.0)
        self.__cog = cog
        self.cookie_data = dict(cookie_data)
        self.opener = opener
        
        self.interaction : Interaction | None = None
        
    async def on_timeout(self):
        if self.interaction:
            await self.interaction.delete_original_response()
            
    async def interaction_check(self, interaction: Interaction):
        if interaction.user.id == self.opener.id:
            return True
        else:
            await interaction.response.send_message("**Pas touche !** · Seul l'auteur de la commande peut ouvrir ce cookie.", ephemeral=True)
            return False
        
    async def start(self, interaction: Interaction):
        embed = self.__cog.embed_cookie(self.cookie_data, hide_content=True)
        await interaction.response.send_message(embed=embed, view=self)
        self.interaction = interaction
        
    @discord.ui.button(label="Ouvrir", style=discord.ButtonStyle.green, emoji='<:iconFortuneCookie:1219724302241235064>')
    async def open_cookie(self, interaction: Interaction, button: discord.ui.Button):
        if not isinstance(interaction.guild, discord.Guild):
            return
        self.__cog.use_cookie(interaction.guild, self.cookie_data['id'])
        self.cookie_data['uses'] += 1
        embed = self.__cog.embed_cookie(self.cookie_data)
        
        new_view = FortuneCookieFlagButton(self.__cog, self.cookie_data, self.opener, self.interaction or interaction)
        await interaction.response.edit_message(embed=embed, view=new_view)
        self.stop()

class FortuneCookieFlagButton(discord.ui.View):
    """Vue d'après ouverture d'un cookie pour le flagger si nécessaire."""
    def __init__(self, cog: 'Messages', cookie_data: dict, opener: discord.Member, original_interaction: Interaction):
        super().__init__(timeout=10.0)
        self.__cog = cog
        self.cookie_data = cookie_data
        self.opener = opener
        
        self.interaction : Interaction = original_interaction
        
    async def on_timeout(self):
        if self.interaction:
            await self.interaction.edit_original_response(view=None)
            
    async def interaction_check(self, interaction: Interaction):
        if interaction.user.id == self.opener.id:
            self.interaction = interaction
            return True
        else:
            await interaction.response.send_message("**Impossible** · Seul l'auteur de la commande peut flagger ce cookie.", ephemeral=True)
            return False
        
    @discord.ui.button(label="Signaler", style=discord.ButtonStyle.danger, emoji=pretty.EMOJIS_ICONS['warning'])
    async def flag_cookie(self, interaction: Interaction, button: discord.ui.Button):
        if not isinstance(interaction.guild, discord.Guild):
            return
        self.__cog.flag_cookie(interaction.guild, self.cookie_data['id'])
        self.cookie_data['flags'] += 1
        embed = self.__cog.embed_cookie(self.cookie_data)
        await interaction.response.edit_message(view=None, embed=embed)
        self.__cog.check_flagged_cookies(interaction.guild)
        
class FortuneCookieDeleteOwnButton(discord.ui.View):
    """Vue pour supprimer un de ses cookie de la fortune."""
    def __init__(self, cog: 'Messages', cookies: list[dict], opener: discord.Member):
        super().__init__(timeout=20.0)
        self.__cog = cog
        self.cookies = cookies
        self.opener = opener
        
        self.interaction : Interaction | None = None
        
        self.pages = self.get_embeds()
        self.index = 0
        self.deleted_indexes = []
        
    def get_embeds(self):
        embeds = [self.__cog.embed_cookie(c, hide_content=False) for c in self.cookies]
        for i, embed in enumerate(embeds):
            embed.set_footer(text=f"Cookie {i+1}/{len(embeds)}")
        return embeds
    
    def is_delete_button_disabled(self, index: int):
        return index in self.deleted_indexes
        
    async def on_timeout(self):
        if self.interaction:
            await self.interaction.delete_original_response()
            
    async def interaction_check(self, interaction: Interaction):
        if interaction.user.id == self.opener.id:
            return True
        else:
            await interaction.response.send_message("**Pas touche !** · Seul l'auteur de la commande peut supprimer ses cookies.", ephemeral=True)
            return False
        
    async def start(self, interaction: Interaction):
        await self.show_page(interaction)
        self.interaction = interaction
        
    async def show_page(self, interaction: Interaction):    
        if not self.pages:
            await interaction.edit_original_response(content="**Plus aucun cookie** · Vous n'avez pas/plus de cookies de la fortune.")
            return
        if not self.pages[self.index]:
            self.index = 0
        await interaction.edit_original_response(embed=self.pages[self.index], view=self)
        
    @discord.ui.button(style=discord.ButtonStyle.blurple, emoji=pretty.EMOJIS_ICONS['back'])
    async def prev_page(self, interaction: Interaction, button: discord.ui.Button):
        self.index = (self.index - 1) % len(self.pages)
        await interaction.response.defer(ephemeral=True)
        
        if self.is_delete_button_disabled(self.index):
            self.delete_cookie.disabled = True
        else:
            self.delete_cookie.disabled = False
        
        await self.show_page(self.interaction or interaction)
        
    @discord.ui.button(label="Supprimer", style=discord.ButtonStyle.danger)
    async def delete_cookie(self, interaction: Interaction, button: discord.ui.Button):
        if not isinstance(interaction.guild, discord.Guild):
            return
        self.__cog.delete_cookie(interaction.guild, self.cookies[self.index]['id'])
        await interaction.response.defer(ephemeral=True)
        current_embed = self.pages[self.index]
        new_embed = current_embed.copy()
        new_embed.color = discord.Color.red()
        new_embed.set_footer(text=f"Cookie {self.index+1}/{len(self.pages)} • Supprimé")
        self.pages[self.index] = new_embed
        self.deleted_indexes.append(self.index)
        self.delete_cookie.disabled = True
        await self.show_page(self.interaction or interaction)
        
    @discord.ui.button(style=discord.ButtonStyle.blurple, emoji=pretty.EMOJIS_ICONS['next'])
    async def next_page(self, interaction: Interaction, button: discord.ui.Button):
        self.index = (self.index + 1) % len(self.pages)
        await interaction.response.defer(ephemeral=True)
        
        if self.is_delete_button_disabled(self.index):
            self.delete_cookie.disabled = True
        else:
            self.delete_cookie.disabled = False
            
        await self.show_page(self.interaction or interaction)
            
    @discord.ui.button(style=discord.ButtonStyle.secondary, emoji=pretty.EMOJIS_ICONS['close'])
    async def close_view(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=None)
        self.stop()

# COG =======================================================

class Messages(commands.Cog):
    """Fonctions amusantes autour de messages personnalisés."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_instance(self)
        
        # Paramètres
        settings = {
            'CookiesPerUserPerDay': 10,
            'FlagsBeforeAutoDeletion': 3,
            'MaxCookieAge': 14
        }
        guild_settings_db = dataio.DictTableDefault('settings', settings)
        
        # Fortune cookies
        cookies_db = dataio.TableDefault(
            """CREATE TABLE IF NOT EXISTS cookies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author_id INTEGER,
                content TEXT,
                created_at INTEGER,
                uses INTEGER DEFAULT 0,
                flags INTEGER DEFAULT 0
                )"""
            )

        self.data.set_defaults(discord.Guild, guild_settings_db, cookies_db)
        
    def cog_unload(self):
        self.data.close_all()
        
    # COOKIES ================================
    # Obtention des cookies
    
    def get_cookies(self, guild: discord.Guild):
        r = self.data.get(guild).fetch_all('''SELECT * FROM cookies''')
        return r
    
    def get_cookie(self, guild: discord.Guild, id: int):
        r = self.data.get(guild).fetch('''SELECT * FROM cookies WHERE id = ?''', (id,))
        return r
    
    def get_random_cookie(self, guild: discord.Guild, weighted: bool = True):
        self.check_old_cookies(guild)
        
        cookies = self.get_cookies(guild)
        if not cookies:
            return None
        if weighted:
            weights = [1 / (c['uses'] + 1) for c in cookies]
            cookie = random.choices(cookies, weights=weights)[0]
        else:
            cookie = random.choice(cookies)
        return cookie
    
    def get_flagged_cookies(self, guild: discord.Guild, threshold: int = 1):
        r = self.data.get(guild).fetch_all('''SELECT * FROM cookies WHERE flags >= ?''', (threshold,))
        return r
    
    # Edition de cookies
    
    def add_cookie(self, author: discord.Member, content: str):
        self.data.get(author.guild).execute(
            '''INSERT INTO cookies (author_id, content, created_at) VALUES (?, ?, ?)''',
            (author.id, content, datetime.now().timestamp())
        )
        
    def delete_cookie(self, guild: discord.Guild, id: int):
        self.data.get(guild).execute('''DELETE FROM cookies WHERE id = ?''', (id,))
        
    def use_cookie(self, guild: discord.Guild, id: int):
        self.data.get(guild).execute('''UPDATE cookies SET uses = uses + 1 WHERE id = ?''', (id,))
        
    def flag_cookie(self, guild: discord.Guild, id: int):
        self.data.get(guild).execute('''UPDATE cookies SET flags = flags + 1 WHERE id = ?''', (id,))
        
    # Affichage de cookies
    
    def embed_cookie(self, cookie: dict, hide_content: bool = False) -> discord.Embed:
        content = cookie['content']
        if hide_content:
            content = '█' * (len(content) // 2 + 1)
        
        embed = discord.Embed(
            description=content,
            color=pretty.DEFAULT_EMBED_COLOR if hide_content else 0xfcab40
        )
        # S'il y a des paramètres (après ?) dans les liens, on les enlève
        img = re.search(r'(https?://[^\s]+)', content)
        if img:
            total_url = img.group(0)
            img_url = total_url.split('?')[0] if '?' in total_url else total_url
            if img_url.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                embed.set_image(url=img_url)
                embed.description = content.replace(total_url, '').strip()
            
        author = self.bot.get_user(cookie['author_id'])
        if not author:
            author = f"ID:{cookie['author_id']}"
        else:
            author = author.name
        embed_text = f"Par {author}"
        if not hide_content:
            embed_text += f" • 👁️ {cookie['uses']} vue{'s' if cookie['uses'] > 1 else ''}"
            if cookie['flags']:
                embed_text += f" • 🚩 {cookie['flags']} signalement{'s' if cookie['flags'] > 1 else ''}"
        embed.set_footer(text=embed_text)
        return embed
    
    # Modération
    
    def get_cookies_by_author(self, guild: discord.Guild, author: discord.Member, *, today: bool = False):
        r = self.data.get(guild).fetch_all('''SELECT * FROM cookies WHERE author_id = ?''', (author.id,))
        if today:
            r = [c for c in r if datetime.now().day == datetime.fromtimestamp(c['created_at']).day]
        return r
    
    def check_flagged_cookies(self, guild: discord.Guild) -> int:
        auto_delete = self.data.get(guild).get_dict_value('settings', 'FlagsBeforeAutoDeletion', cast=int)
        flagged = self.get_flagged_cookies(guild, auto_delete)
        if flagged:
            for cookie in flagged:
                self.delete_cookie(guild, cookie['id'])
            return len(flagged)
        return 0
    
    def check_old_cookies(self, guild: discord.Guild) -> int:
        max_age = self.data.get(guild).get_dict_value('settings', 'MaxCookieAge', cast=int)
        max_age *= 86400
        cookies = self.get_cookies(guild)
        old = [c for c in cookies if datetime.now().timestamp() - c['created_at'] > max_age]
        if old:
            for cookie in old:
                self.delete_cookie(guild, cookie['id'])
            return len(old)
        return 0
        
    # COMMANDES =======================================================
    
    fortune_group = app_commands.Group(
        name="fortune", 
        description="Cookies de la fortune", 
        guild_only=True)
    
    @fortune_group.command(name="get")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 300, key=lambda i: (i.guild_id, i.user.id))
    async def cmd_fortune_get(self, interaction: Interaction):
        """Ouvre un cookie de la fortune contenant un message créé par un membres du serveur"""
        if not isinstance(interaction.guild, discord.Guild) or not isinstance(interaction.user, discord.Member):
            return
        cookie = self.get_random_cookie(interaction.guild)
        if not cookie:
            await interaction.response.send_message("**Pas de cookie** · Il n'y a pas de cookies de la fortune sur ce serveur.", ephemeral=True)
            return
        
        view = FortuneCookieBaseView(self, cookie, interaction.user)
        await view.start(interaction)
    
    @fortune_group.command(name="submit")
    @app_commands.rename(content='contenu')
    async def cmd_manage_add(self, interaction: Interaction, content: str):
        """Proposer un nouveau cookie de la fortune
        
        :param content: Contenu du cookie de la fortune (markdown supporté, max. 500 caractères)"""
        if not isinstance(interaction.guild, discord.Guild) or not isinstance(interaction.user, discord.Member):
            return
        
        author_today = self.get_cookies_by_author(interaction.guild, interaction.user, today=True)
        if len(author_today) >= self.data.get(interaction.guild).get_dict_value('settings', 'CookiesPerUserPerDay', cast=int):
            await interaction.response.send_message(f"**Trop de cookies** · Vous avez déjà atteint la limite de cookies pour aujourd'hui. Vous pouvez en soumettre jusqu'à {self.data.get(interaction.guild).get_dict_value('settings', 'CookiesPerUserPerDay', cast=int)}.", ephemeral=True)
            return
        
        content = content.strip()
        links = re.findall(r'(https?://[^\s]+)', content)
        content_lenght = len(content) - sum(len(link) for link in links)
        if content_lenght > 500:
            await interaction.response.send_message(f"**Contenu trop long** · Votre cookie de la fortune ne peut pas dépasser 500 caractères (liens non inclus).", ephemeral=True)
            return
        
        all_cookies = self.get_cookies(interaction.guild)
        if content.lower() in [c['content'].lower() for c in all_cookies]:
            await interaction.response.send_message(f"**Contenu déjà existant** · Un cookie de la fortune avec ce contenu existe déjà sur ce serveur.", ephemeral=True)
            return
        
        # Générer une prévisualisation
        embed = self.embed_cookie({'content': content, 'author_id': interaction.user.id, 'uses': 0, 'flags': 0}, hide_content=False)
        confirm = interface.ConfirmationView(confirm_label="Valider", users=[interaction.user])
        await interaction.response.send_message("**Prévisualisation** · Est-ce que l'affichage vous convient ?", embed=embed, ephemeral=True, view=confirm)
        await confirm.wait()
        if not confirm.value:
            await interaction.delete_original_response()
            return await interaction.followup.send("**Annulé** · La création du cookie a été annulée.", ephemeral=True)
        
        self.add_cookie(interaction.user, content)
        rankio.get(interaction.user).add_points(10)
        await interaction.edit_original_response(content="**Cookie ajouté** · Votre cookie de la fortune a été ajouté avec succès.\nVous gagnez **10 points de prestige (✱)** pour votre contribution !", view=None)
        
    @fortune_group.command(name="list")
    @app_commands.rename(author='auteur', flagged='signalés')
    async def cmd_fortune_list(self, interaction: Interaction, author: discord.Member | None = None, flagged: bool = False):
        """Affiche la liste des cookies de la fortune

        :param author: Membre dont les cookies sont à afficher, par défaut tous
        :param flagged: Si True, affiche seulement les cookies signalés, par défaut False"""
        if not isinstance(interaction.guild, discord.Guild) or not isinstance(interaction.user, discord.Member):
            return
        
        cookies = self.get_cookies(interaction.guild)
        if flagged:
            cookies = [c for c in cookies if c['flags'] > 0]
        if author:
            cookies = [c for c in cookies if c['author_id'] == author.id]
        if not cookies:
            await interaction.response.send_message("**Aucun cookie** · Il n'y a pas de cookies de la fortune avec ces critères.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        mod_privilege = interaction.user.guild_permissions.manage_messages
        
        chunks = []
        for c in cookies:
            cookie_author = self.bot.get_user(c['author_id'])
            if not cookie_author:
                nickname = f"ID:{c['author_id']}"
            else:
                nickname = cookie_author.name
            content = pretty.shorten_text(c['content'], 50)
            if cookie_author != interaction.user:
                if not mod_privilege:
                    content = '█' * (len(content) // 2 + 1)
                else:
                    content = f"||{content}||"
            chunks.append(f"`{c['id']}` ({nickname}) · {content} [👁️ {c['uses']} · 🚩 {c['flags']}]")
        
        embeds = []
        for i in range(0, len(chunks), 10):
            embed = discord.Embed(
                title="Cookies de la fortune du serveur",
                description="\n".join(chunks[i:i+10]),
                color=0xfcab40
            )
            embed.set_footer(text=f"Page {i//10 + 1}/{len(chunks)//10 + 1} • {len(cookies)} cookies")
            embeds.append(embed)
            
        await interface.EmbedPaginatorMenu(embeds=embeds, users=[interaction.user]).start(interaction)
        
    @fortune_group.command(name="review")
    async def cmd_fortune_review(self, interaction: Interaction):
        """Affiche la liste de vos cookies de la fortune et vous permet de les supprimer"""
        if not isinstance(interaction.guild, discord.Guild) or not isinstance(interaction.user, discord.Member):
            return
        
        cookies = self.get_cookies_by_author(interaction.guild, interaction.user)
        if not cookies:
            await interaction.response.send_message("**Aucun cookie** · Vous n'avez pas encore créé de cookies de la fortune.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        view = FortuneCookieDeleteOwnButton(self, cookies, interaction.user)
        await view.start(interaction)
        
    fortune_mod_group = app_commands.Group(
        name="manage-fortune",
        description="Modération des cookies de la fortune", 
        guild_only=True,
        default_permissions=discord.Permissions(manage_messages=True))
    
    @fortune_mod_group.command(name="delete")
    @app_commands.rename(cookie_id='id_cookie')
    async def cmd_fortune_delete(self, interaction: Interaction, cookie_id: int):
        """Supprime un cookie de la fortune
        
        :param cookie_id: ID du cookie à supprimer"""
        if not isinstance(interaction.guild, discord.Guild):
            return
        cookie = self.get_cookie(interaction.guild, cookie_id)
        if not cookie:
            await interaction.response.send_message(f"**Cookie introuvable** · Le cookie `ID:{cookie_id}` n'existe pas.", ephemeral=True)
            return
        
        self.delete_cookie(interaction.guild, cookie_id)
        await interaction.response.send_message(f"**Cookie supprimé** · Le cookie `ID:{cookie_id}` a été supprimé avec succès.", ephemeral=True)
        
    @cmd_fortune_delete.autocomplete('cookie_id')
    async def autocomplete_cookie_id(self, interaction: Interaction, current: str):
        if not isinstance(interaction.guild, discord.Guild):
            return []
        cookies = self.get_cookies(interaction.guild)
        cookies = [(c['id'], pretty.shorten_text(c['content'], 25)) for c in cookies]
        r = fuzzy.finder(current, cookies, key=lambda c: f"{c[0]} {c[1]}")
        return [app_commands.Choice(name=f"{c[0]} · {c[1]}", value=c[0]) for c in r]
        
    @fortune_mod_group.command(name="guildsettings")
    @app_commands.choices(key=[
        app_commands.Choice(name="Ajouts autorisés par jour", value="CookiesPerUserPerDay"),
        app_commands.Choice(name="Signalements avant suppr. auto.", value="FlagsBeforeAutoDeletion"),
        app_commands.Choice(name="Âge maximal des cookies (en jours)", value="MaxCookieAge")
    ])
    @app_commands.rename(key='option', value='valeur')
    async def cmd_fortune_settings(self, interaction: Interaction, key: str, value: int):
        """Modifie les paramètres des cookies de la fortune
        
        :param key: Paramètre à modifier
        :param value: Valeur du paramètre"""
        if not isinstance(interaction.guild, discord.Guild):
            return
        settings_dict = self.data.get(interaction.guild).get_dict_values('settings')
        if key not in settings_dict:
            await interaction.response.send_message(f"**Paramètre inconnu** · Le paramètre `{key}` n'existe pas.", ephemeral=True)
            return
        if value <= 0:
            await interaction.response.send_message(f"**Valeur invalide** · La valeur du paramètre `{key}` doit être positive.", ephemeral=True)
            return

        self.data.get(interaction.guild).set_dict_value('settings', key, int(value))
        await interaction.response.send_message(f"**Paramètre modifié** · Le paramètre `{key}` a été modifié avec succès : `{settings_dict[key]}` → `{value}`.", ephemeral=True)

        
async def setup(bot):
    await bot.add_cog(Messages(bot))
