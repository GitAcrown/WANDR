"""
### Système centralisé de suivi d'activité des membres et de ranking
A utiliser en important le module `rankio` dans les cogs concernés.
"""

from datetime import datetime, timedelta
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable, Sequence, overload

import discord
from discord.ext import commands

PRESTIGE_SYMB = '✱'
DB_PATH = Path('common/public')
_RANKINGS : dict[int, 'GuildRanking'] = {}

class GuildRanking:
    def __init__(self, guild: discord.Guild):
        """Classe de gestion des rankings par serveur

        :param guild: Serveur Discord concerné
        """
        self.guild = guild
        self.db = Path(f'common/public/Ranking_{guild.id}.db')
        
        self._conn = self._connect()
        self._initialize()
        
        self.__members : dict[int, MemberRanking] = {}
        
    def __repr__(self) -> str:
        return f'<GuildRanking guild={self.guild!r}>'
        
    def __del__(self):
        self._conn.close() 
        
    # --- Base de données ---
        
    def _connect(self) -> sqlite3.Connection:
        if not DB_PATH.exists():
            DB_PATH.mkdir()
        db = sqlite3.connect(self.db)
        db.row_factory = sqlite3.Row
        return db
    
    def _initialize(self):
        with closing(self._conn.cursor()) as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ranking (
                    user_id INTEGER,
                    date TEXT,
                    points INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, date)
                )
            """)
            self._conn.commit()
        
    # --- Membres du serveur ---
    
    def get_member(self, member: discord.Member) -> 'MemberRanking':
        """Récupère le classement d'un membre du serveur

        :param member: Membre Discord concerné
        :return: Classement du membre
        """
        if member.id not in self.__members:
            self.__members[member.id] = MemberRanking(member, self._conn)
        return self.__members[member.id]
    
    # --- Classement ---
    
    def get_top(self, days: int = 7, limit: int = 10) -> Sequence[tuple[discord.Member, int]]:
        """Récupère le classement des membres du serveur sur une période donnée
        
        :param days: Nombre de jours à prendre en compte
        :param limit: Nombre de membres à afficher
        :return: Liste des membres classés"""
        date = datetime.now() - timedelta(days=days)
        members_ids = {m.id: m for m in self.guild.members}
        with closing(self._conn.cursor()) as cursor:
            cursor.execute("""
                SELECT user_id, SUM(points) AS total_points
                FROM ranking
                WHERE date >= ?
                GROUP BY user_id
                ORDER BY total_points DESC
                LIMIT ?
            """, (date.strftime('%Y-%m-%d'), limit))
            return [(members_ids[row['user_id']], row['total_points']) for row in cursor.fetchall()]    
    

class MemberRanking:
    def __init__(self, member: discord.Member, conn: sqlite3.Connection):
        self.member = member
        self.conn = conn
        
        self.__points : dict[str, int] = {}
        
    def __repr__(self) -> str:
        return f'<MemberRanking member={self.member!r}>'
    
    # --- Points ---
    
    def __load_points(self):
        with closing(self.conn.cursor()) as cursor:
            cursor.execute("""
                SELECT date, points FROM ranking WHERE user_id = ?
            """, (self.member.id,))
            for row in cursor.fetchall():
                self.__points[row['date']] = row['points']
                
    def get_points(self, date: datetime | str | None = None) -> int:
        """Récupère le nombre de points d'un membre à une date donnée

        :param date: Date à laquelle récupérer les points (par défaut, aujourd'hui)
        :return: Nombre de points du membre à la date donnée
        """
        if not date:
            date = datetime.now().strftime('%Y-%m-%d')
        if isinstance(date, datetime):
            date = date.strftime('%Y-%m-%d')
        if date not in self.__points:
            self.__load_points()
        return self.__points.get(date, 0)
    
    def get_total_points(self) -> int:
        """Récupère le nombre total de points d'un membre
        
        :return: Nombre total de points du membre"""
        with closing(self.conn.cursor()) as cursor:
            cursor.execute("""
                SELECT SUM(points) AS total_points
                FROM ranking
                WHERE user_id = ?
            """, (self.member.id,))
            return cursor.fetchone()['total_points'] or 0
    
    def get_cumulative_points(self, *, start: datetime | str | None = None, end: datetime | str | None = None) -> int:
        """Récupère le nombre de points cumulés d'un membre sur une période donnée
        
        :param start: Date de début (par défaut, 7 jours avant aujourd'hui)
        :param end: Date de fin (par défaut, aujourd'hui)
        :return: Nombre de points cumulés du membre sur la période donnée"""
        if not start:
            start = datetime.now() - timedelta(days=7)
        if not end:
            end = datetime.now()
        if isinstance(start, datetime):
            start = start.strftime('%Y-%m-%d')
        if isinstance(end, datetime):
            end = end.strftime('%Y-%m-%d')
        
        with closing(self.conn.cursor()) as cursor:
            cursor.execute("""
                SELECT SUM(points) AS total_points
                FROM ranking
                WHERE user_id = ? AND date BETWEEN ? AND ?
            """, (self.member.id, start, end))
            return cursor.fetchone()['total_points'] or 0
    
    def set_points(self, points: int, date: datetime | str | None = None):
        """Définit le nombre de points d'un membre à une date donnée
        
        :param points: Nombre de points
        :param date: Date à laquelle définir les points (par défaut, aujourd'hui)"""
        if not date:
            date = datetime.now().strftime('%Y-%m-%d')
        if isinstance(date, datetime):
            date = date.strftime('%Y-%m-%d')
        self.__points[date] = points
        
        with closing(self.conn.cursor()) as cursor:
            cursor.execute("""
                INSERT OR REPLACE INTO ranking (user_id, date, points) VALUES (?, ?, ?)
            """, (self.member.id, date, points))
            self.conn.commit()
            
    def add_points(self, points: int, date: datetime | str | None = None):
        """Ajoute des points à un membre

        :param points: Nombre de points à ajouter
        :param date: Date à laquelle ajouter les points (par défaut, aujourd'hui)
        """
        self.set_points(self.get_points(date) + points, date)
        
    def remove_points(self, points: int, date: datetime | str | None = None):
        """Retire des points à un membre
        
        :param points: Nombre de points à retirer
        :param date: Date à laquelle retirer les points (par défaut, aujourd'hui)
        """
        self.set_points(self.get_points(date) - points, date)
        
    # --- Ranking ---
    
    def get_personal_rank(self, days: int = 7) -> int:
        """Récupère le rang personnel d'un membre sur une période donnée
        
        :param days: Nombre de jours à prendre en compte
        :return: Rang personnel du membre"""
        date = datetime.now() - timedelta(days=days)
        with closing(self.conn.cursor()) as cursor:
            cursor.execute("""
                SELECT user_id, SUM(points) AS total_points
                FROM ranking
                WHERE date >= ?
                GROUP BY user_id
                ORDER BY total_points DESC
            """, (date.strftime('%Y-%m-%d'),))
            rows = cursor.fetchall()
            return next((i for i, row in enumerate(rows) if row['user_id'] == self.member.id), -1) + 1
        
    # --- Nettoyage ---
    
    def cleanup(self, days: int = 30):
        """Efface toutes les données de ranking de plus de `days` jours
        
        :param days: Nombre de jours à conserver"""
        date = datetime.now() - timedelta(days=days)
        with closing(self.conn.cursor()) as cursor:
            cursor.execute("""
                DELETE FROM ranking WHERE user_id = ? AND date < ?
            """, (self.member.id, date.strftime('%Y-%m-%d')))
            self.conn.commit()
    
    def clear_all(self):
        """Efface toutes les données de ranking du membre"""
        with closing(self.conn.cursor()) as cursor:
            cursor.execute("""
                DELETE FROM ranking WHERE user_id = ?
            """, (self.member.id,))
            self.conn.commit()
            
# ===== ACCES AUX DONNEES =====

@overload
def get(obj: discord.Guild) -> GuildRanking: ...

@overload
def get(obj: discord.Member) -> MemberRanking: ...

def get(obj: discord.Guild | discord.Member) -> GuildRanking | MemberRanking:
    """Récupère le classement d'un serveur ou d'un membre
    
    :param obj: Serveur ou membre Discord concerné
    :return: Classement"""
    if isinstance(obj, discord.Guild):
        if obj.id not in _RANKINGS:
            _RANKINGS[obj.id] = GuildRanking(obj)
        return _RANKINGS[obj.id]
    elif isinstance(obj, discord.Member):
        return get(obj.guild).get_member(obj)
    else:
        raise TypeError(f'Invalid type {type(obj)} for get_ranking()')
