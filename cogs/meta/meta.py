import logging
import random
import re
from datetime import datetime, timedelta

from IPython import embed
import discord
from discord import Interaction, app_commands
from discord.ext import commands

from common import rankio
from common.utils import fuzzy, pretty, interface

logger = logging.getLogger(f'WANDR.{__name__.split(".")[-1]}')

# COG =======================================================

class Meta(commands.Cog):
    """Contrôleur du système de prestige et de ranking interne."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        
        self.show_prestige = app_commands.ContextMenu(
            name='Prestige',
            callback=self.callback_show_prestige,
            extras={'description': "Affiche le prestige d'un membre."}
        )
        self.bot.tree.add_command(self.show_prestige)
        
    # --- Affichage ---
    
    def _member_ranking_embed(self, member: discord.Member):
        ranking = rankio.get(member)
        username = f"***{member.display_name}*** ({member.name})" if member.display_name != member.name else f'***{member.name}***'
        embed = discord.Embed(
            title=username,
            color=member.color
        )
        # Total
        embed.add_field(
            name='Total',
            value=pretty.codeblock(str(ranking.get_total_points()) + '✱')
        )
        
        # Cumul sur 7 jours
        points = ranking.get_cumulative_points()
        embed.add_field(
            name='Points/7j',
            value=pretty.codeblock(str(points) + '✱', lang='css')
        )
        
        # Evolution par rapport à la veille
        two_days_ago = datetime.now() - timedelta(days=2)
        yesterday = datetime.now() - timedelta(days=1)
        points_two_days_ago = ranking.get_cumulative_points(start=two_days_ago, end=yesterday)
        points_yesterday = ranking.get_cumulative_points(start=yesterday)
        diff = points_yesterday - points_two_days_ago
        embed.add_field(
            name='Tendance',
            value=pretty.codeblock(f'{diff:+}', lang='diff')
        )

        embed.set_thumbnail(url=member.display_avatar.url)
        return embed
    
    # COMMANDES ==============================================
    
    @app_commands.command(name='account')
    @app_commands.guild_only()
    async def command_account(self, interaction: Interaction, member: discord.Member | None = None):
        """Affiche les informations de compte d'un membre."""
        if not isinstance(interaction.user, discord.Member):
            return
        user = member or interaction.user
        embed = self._member_ranking_embed(user)
        await interaction.response.send_message(embed=embed)
        
    @app_commands.command(name='leaderboard')
    @app_commands.guild_only()
    @app_commands.rename(limit='limite')
    async def command_leaderboard(self, interaction: Interaction, limit: app_commands.Range[int, 1, 30] = 10):
        """Affiche le classement des membres."""
        if not isinstance(interaction.guild, discord.Guild):
            return
        ranking = rankio.get(interaction.guild).get_top(limit=limit)
        text = '\n'.join(f'{i+1}. ***{member.name}*** · {points}✱' for i, (member, points) in enumerate(ranking))
        embed = discord.Embed(
            title='Classement de **prestige** (7 derniers jours)',
            description=text
        )
        # Classement personnel
        if isinstance(interaction.user, discord.Member):
            own_rank = rankio.get(interaction.user).get_personal_rank()
            embed.set_footer(text=f'Votre classement · {own_rank}e')
        
        embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild.icon else None)
        await interaction.response.send_message(embed=embed)
        
    # CALLBACKS ==============================================
    
    async def callback_show_prestige(self, interaction: Interaction, member: discord.Member):
        embed = self._member_ranking_embed(member)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
async def setup(bot):
    await bot.add_cog(Meta(bot))
