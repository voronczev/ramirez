import os
import json
import math
import asyncio
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta, time as dtime

load_dotenv()

TOKEN = os.getenv("TOKEN")

EMBED_COLOR = discord.Color(int("323237", 16))
FOOTER_TEXT = "Ramirez  ·  Majestic RP"
MSK_TZ = timezone(timedelta(hours=3))

HOUSE_PAYMENT_CHANNEL_ID = 1488210182910513303
ACTIVITY_CHANNEL_ID = 1488554549839925479

PLUS_COMMAND_ROLES = [
    703948034995912825,
    703947807245074437,
    1478490780266926231
]

DELETE_PARTICIPANT_ROLES = [
    703948034995912825,
    703947807245074437,
    1478490780266926231
]

ADMIN_ROLES = [
    703948034995912825,
    703947807245074437
]

DATA_DIR = "data"
PAYMENTS_FILE = os.path.join(DATA_DIR, "payments.json")
VOICE_FILE = os.path.join(DATA_DIR, "voice_activity.json")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


voice_totals = load_json(VOICE_FILE, {})
active_sessions: dict[int, datetime] = {}
active_plus_views: list["PlusView"] = []
active_contract_views: list["ContractView"] = []


def has_any_role(member, role_ids) -> bool:
    return any(role.id in role_ids for role in getattr(member, "roles", []))


def today_msk() -> str:
    return datetime.now(MSK_TZ).strftime("%d.%m.%Y")


def validate_date(value: str) -> str | None:
    try:
        return datetime.strptime(value.strip(), "%d.%m.%Y").strftime("%d.%m.%Y")
    except ValueError:
        return None


def parse_date_to_utc(date_str: str) -> datetime | None:
    formats = ["%d.%m.%Y %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M", "%d.%m.%Y", "%d/%m/%Y"]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.replace(tzinfo=MSK_TZ)
        except ValueError:
            continue
    return None


def get_time_remaining(end_time: datetime) -> str:
    now = datetime.now(timezone.utc)
    delta = end_time - now
    if delta.total_seconds() <= 0:
        return "(Сбор завершен)"
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days > 0:
        return f"(осталось: {days} дн.)"
    elif hours > 0:
        return f"(осталось: {hours} ч.)"
    elif minutes > 0:
        return f"(осталось: {minutes} мин.)"
    return f"(осталось: {seconds} сек.)"


def format_duration(seconds) -> str:
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}h {m}m {s}s"


def total_seconds_for(user_id: int) -> float:
    base = voice_totals.get(str(user_id), 0)
    if user_id in active_sessions:
        base += (datetime.now(timezone.utc) - active_sessions[user_id]).total_seconds()
    return base


def get_ranking() -> list[tuple[str, float]]:
    ids = set(voice_totals.keys()) | {str(uid) for uid in active_sessions}
    data = [(uid, total_seconds_for(int(uid))) for uid in ids]
    data.sort(key=lambda item: item[1], reverse=True)
    return data


def flush_active_sessions():
    now = datetime.now(timezone.utc)
    changed = False
    for uid, start in list(active_sessions.items()):
        elapsed = (now - start).total_seconds()
        voice_totals[str(uid)] = voice_totals.get(str(uid), 0) + elapsed
        active_sessions[uid] = now
        changed = True
    if changed:
        save_json(VOICE_FILE, voice_totals)


def payment_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Оплатите налоги на семейный дом!",
        color=EMBED_COLOR,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=FOOTER_TEXT)
    return embed


async def send_payment_dm(user_id: int):
    try:
        user = await bot.fetch_user(user_id)
        await user.send(embed=payment_embed())
    except Exception as e:
        print(f"❌ Не удалось отправить ЛС пользователю {user_id}: {e}")


async def send_payment_channel(user_id: int):
    if not HOUSE_PAYMENT_CHANNEL_ID:
        return
    channel = bot.get_channel(HOUSE_PAYMENT_CHANNEL_ID)
    if not channel:
        try:
            channel = await bot.fetch_channel(HOUSE_PAYMENT_CHANNEL_ID)
        except discord.NotFound:
            print("❌ Канал для уведомлений об оплате не найден!")
            return
    await channel.send(f"<@{user_id}>", embed=payment_embed())


async def send_payment_notification(user_id: int, action: str, date_str: str = None):
    """Отправляет уведомление в канал оплаты о добавлении/удалении пользователя."""
    if not HOUSE_PAYMENT_CHANNEL_ID:
        return
    channel = bot.get_channel(HOUSE_PAYMENT_CHANNEL_ID)
    if not channel:
        try:
            channel = await bot.fetch_channel(HOUSE_PAYMENT_CHANNEL_ID)
        except discord.NotFound:
            print("❌ Канал для уведомлений об оплате не найден!")
            return
    
    if action == "added":
        embed = discord.Embed(
            title="Новый участник добавлен в список оплаты",
            description=f"<@{user_id}> был добавлен в список оплаты дома.\n**Дата уведомления**: {date_str}",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
    elif action == "removed":
        embed = discord.Embed(
            title="Участник удален из списка оплаты",
            description=f"<@{user_id}> был удален из списка оплаты дома.",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
    elif action == "expired":
        embed = discord.Embed(
            title="Автоматическое удаление (Просрочка)",
            description=f"<@{user_id}> удален из списка, так как с даты оплаты ({date_str}) прошло более 7 дней.",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc)
        )
    else:
        return
    
    embed.set_footer(text=FOOTER_TEXT)
    try:
        await channel.send(embed=embed)
    except Exception as e:
        print(f"Ошибка отправки уведомления: {e}")


def clean_expired_payments(payments: dict) -> tuple[dict, list[tuple[int, str]]]:
    """
    Безопасно удаляет записи, где с даты оплаты прошло более 7 дней.
    Возвращает обновленный словарь и список кортежей (ID, Дата) удаленных пользователей.
    """
    now = datetime.now(MSK_TZ)
    removed_info = [] # Храним (uid, date_str) для уведомлений
    keys_to_remove = []

    # Сначала собираем ключи для удаления, чтобы не менять словарь во время итерации
    for uid, entry in payments.items():
        date_str = entry.get("date")
        if not date_str:
            continue
        
        try:
            payment_date = datetime.strptime(date_str, "%d.%m.%Y").replace(tzinfo=MSK_TZ)
            delta = now - payment_date
            
            if delta.days > 7:
                keys_to_remove.append(uid)
                removed_info.append((int(uid), date_str))
        except ValueError:
            continue

    # Теперь удаляем
    for key in keys_to_remove:
        del payments[key]
        
    return payments, removed_info


async def run_payment_reminders():
    payments = load_json(PAYMENTS_FILE, {})
    if not payments:
        return
    today = today_msk()
    changed = False
    for uid, entry in payments.items():
        if entry.get("date") != today:
            continue
        await send_payment_dm(int(uid))
        if entry.get("channel_ping_date") != today:
            await send_payment_channel(int(uid))
            entry["channel_ping_date"] = today
            changed = True
    if changed:
        save_json(PAYMENTS_FILE, payments)


@tasks.loop(time=[
    dtime(hour=12, minute=0, tzinfo=MSK_TZ),
    dtime(hour=16, minute=0, tzinfo=MSK_TZ),
    dtime(hour=20, minute=0, tzinfo=MSK_TZ)
])
async def payment_reminder_loop():
    await run_payment_reminders()


@tasks.loop(minutes=5)
async def voice_flush_loop():
    flush_active_sessions()


@tasks.loop(seconds=20)
async def plus_expiry_loop():
    now = datetime.now(timezone.utc)
    
    # Обработка Contract View (старая логика /plus, теперь /contract)
    for view in list(active_contract_views):
        if view.end_time is None:
            active_contract_views.remove(view)
            continue
        if now < view.end_time:
            continue
        if view.message is not None and view.message.embeds:
            embed = view.message.embeds[0].copy()
            render_contract_embed(view, embed)
            try:
                await view.message.edit(embed=embed, view=view)
            except Exception as e:
                print(f"⚠️ Не удалось обновить сбор после истечения времени: {e}")
        active_contract_views.remove(view)

    # Обработка Plus View (новая логика /plus)
    for view in list(active_plus_views):
        if view.end_time is None:
            active_plus_views.remove(view)
            continue
        if now < view.end_time:
            continue
        if view.message is not None and view.message.embeds:
            embed = view.message.embeds[0].copy()
            render_plus_embed(view, embed)
            try:
                await view.message.edit(embed=embed, view=view)
            except Exception as e:
                print(f"⚠️ Не удалось обновить PLUS сбор после истечения времени: {e}")
        active_plus_views.remove(view)


# --- Логика для CONTRACT (бывший /plus) ---

def render_contract_embed(view: "ContractView", embed: discord.Embed) -> discord.Embed:
    members_text = "\n".join(f"<@{uid}>" for uid in view.participants) or "Пока никто не записался."
    found_index = None
    for i, field in enumerate(embed.fields):
        if field.name == "Участники":
            found_index = i
            break
    if found_index is not None:
        embed.set_field_at(found_index, name="Участники", value=members_text, inline=False)
    else:
        embed.add_field(name="Участники", value=members_text, inline=False)

    remaining = view.max_slots - len(view.participants)
    button = view.join_button
    if view.is_expired():
        button.disabled = True
        button.style = discord.ButtonStyle.secondary
        button.label = "Время истекло"
    elif remaining <= 0:
        button.disabled = True
        button.style = discord.ButtonStyle.secondary
        button.label = "Мест нет"
    else:
        button.disabled = False
        button.style = discord.ButtonStyle.primary
        button.label = f"Записаться ({remaining} мест)"
    return embed


class ContractJoinButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Записаться", style=discord.ButtonStyle.primary, custom_id="contract_join_btn")

    async def callback(self, interaction: discord.Interaction):
        view: ContractView = self.view
        if view.is_expired():
            await interaction.response.send_message("Время записи на этот сбор истекло.", ephemeral=True)
            embed = interaction.message.embeds[0].copy()
            render_contract_embed(view, embed)
            try:
                await interaction.message.edit(embed=embed, view=view)
            except Exception:
                pass
            return
        if interaction.user.id in view.participants:
            await interaction.response.send_message("Вы уже записаны на этот сбор.", ephemeral=True)
            return
        if len(view.participants) >= view.max_slots:
            await interaction.response.send_message("Все слоты заняты. Сбор укомплектован.", ephemeral=True)
            return
        view.participants.append(interaction.user.id)
        embed = interaction.message.embeds[0].copy()
        render_contract_embed(view, embed)
        await interaction.response.edit_message(embed=embed, view=view)


class ContractDeleteParticipantButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Удалить участника", style=discord.ButtonStyle.danger, custom_id="contract_delete_btn")

    async def callback(self, interaction: discord.Interaction):
        view: ContractView = self.view
        if not has_any_role(interaction.user, DELETE_PARTICIPANT_ROLES):
            await interaction.response.send_message("У вас нет прав для удаления участников.", ephemeral=True)
            return
        if not view.participants:
            await interaction.response.send_message("Список участников пуст.", ephemeral=True)
            return
        remove_view = RemoveParticipantView(view, interaction.message, interaction.guild)
        await interaction.response.send_message("Выберите участника для удаления:", view=remove_view, ephemeral=True)


class ContractView(discord.ui.View):
    def __init__(self, max_slots: int, author_id: int, end_time: datetime | None = None):
        super().__init__(timeout=None)
        self.max_slots = max_slots
        self.author_id = author_id
        self.end_time = end_time
        self.message: discord.Message | None = None
        self.participants: list[int] = []
        self.join_button = ContractJoinButton()
        self.delete_button = ContractDeleteParticipantButton()
        self.add_item(self.join_button)
        self.add_item(self.delete_button)

    def is_expired(self) -> bool:
        return self.end_time is not None and datetime.now(timezone.utc) >= self.end_time


# --- Логика для NEW PLUS (/plus с ручным добавлением) ---

def render_plus_embed(view: "PlusView", embed: discord.Embed) -> discord.Embed:
    members_text = "\n".join(f"<@{uid}>" for uid in view.participants) or "Пока никто не записался."
    found_index = None
    for i, field in enumerate(embed.fields):
        if field.name == "Участники":
            found_index = i
            break
    if found_index is not None:
        embed.set_field_at(found_index, name="Участники", value=members_text, inline=False)
    else:
        embed.add_field(name="Участники", value=members_text, inline=False)

    remaining = view.max_slots - len(view.participants)
    add_button = view.add_participant_button
    delete_button = view.delete_button
    
    if view.is_expired():
        add_button.disabled = True
        add_button.style = discord.ButtonStyle.secondary
        add_button.label = "Время истекло"
        delete_button.disabled = True
    elif remaining <= 0:
        add_button.disabled = True
        add_button.style = discord.ButtonStyle.secondary
        add_button.label = "Мест нет"
        # Delete button stays enabled to allow removal even if full
    else:
        add_button.disabled = False
        add_button.style = discord.ButtonStyle.primary
        add_button.label = "Добавить участника"
        
    return embed


class PlusAddParticipantButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Добавить участника", style=discord.ButtonStyle.primary, custom_id="plus_add_btn")

    async def callback(self, interaction: discord.Interaction):
        view: PlusView = self.view
        if not has_any_role(interaction.user, PLUS_COMMAND_ROLES):
            await interaction.response.send_message("У вас нет прав для добавления участников.", ephemeral=True)
            return
            
        if view.is_expired():
            await interaction.response.send_message("Время сбора истекло.", ephemeral=True)
            return

        if len(view.participants) >= view.max_slots:
            await interaction.response.send_message("Все слоты заняты.", ephemeral=True)
            return

        add_view = AddParticipantSelectView(view, interaction.message, interaction.guild)
        await interaction.response.send_message("Выберите участника для добавления:", view=add_view, ephemeral=True)


class PlusDeleteParticipantButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Удалить участника", style=discord.ButtonStyle.danger, custom_id="plus_delete_btn")

    async def callback(self, interaction: discord.Interaction):
        view: PlusView = self.view
        if not has_any_role(interaction.user, DELETE_PARTICIPANT_ROLES):
            await interaction.response.send_message("У вас нет прав для удаления участников.", ephemeral=True)
            return
        if not view.participants:
            await interaction.response.send_message("Список участников пуст.", ephemeral=True)
            return
        remove_view = RemoveParticipantView(view, interaction.message, interaction.guild)
        await interaction.response.send_message("Выберите участника для удаления:", view=remove_view, ephemeral=True)


class AddParticipantSelect(discord.ui.Select):
    def __init__(self, plus_view: "PlusView", origin_message: discord.Message, guild: discord.Guild | None):
        self.plus_view = plus_view
        self.origin_message = origin_message
        
        # Получаем всех участников сервера, которых еще нет в списке
        all_members = guild.members if guild else []
        options = []
        count = 0
        for member in all_members:
            if member.id not in plus_view.participants and not member.bot:
                if count >= 25: break # Discord limit for select options
                options.append(discord.SelectOption(label=member.display_name[:100], value=str(member.id)))
                count += 1
                
        super().__init__(placeholder="Выберите участника", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if len(self.plus_view.participants) >= self.plus_view.max_slots:
             await interaction.response.send_message("Слоты заполнились пока вы выбирали.", ephemeral=True)
             return

        uid = int(self.values[0])
        if uid not in self.plus_view.participants:
            self.plus_view.participants.append(uid)
            
        embed = self.origin_message.embeds[0].copy()
        render_plus_embed(self.plus_view, embed)
        await self.origin_message.edit(embed=embed, view=self.plus_view)
        
        member = interaction.guild.get_member(uid) if interaction.guild else None
        name = member.display_name if member else str(uid)
        await interaction.response.edit_message(content=f"Участник **{name}** добавлен в сбор.", view=None)


class AddParticipantSelectView(discord.ui.View):
    def __init__(self, plus_view: "PlusView", origin_message: discord.Message, guild: discord.Guild | None):
        super().__init__(timeout=120)
        self.add_item(AddParticipantSelect(plus_view, origin_message, guild))


class RemoveParticipantSelect(discord.ui.Select):
    def __init__(self, plus_view: "PlusView", origin_message: discord.Message, guild: discord.Guild | None):
        self.plus_view = plus_view
        self.origin_message = origin_message
        options = []
        for uid in plus_view.participants[:25]:
            member = guild.get_member(uid) if guild else None
            name = member.display_name if member else str(uid)
            options.append(discord.SelectOption(label=name[:100], value=str(uid)))
        super().__init__(placeholder="Участник для удаления", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        uid = int(self.values[0])
        if uid in self.plus_view.participants:
            self.plus_view.participants.remove(uid)
        embed = self.origin_message.embeds[0].copy()
        render_plus_embed(self.plus_view, embed)
        await self.origin_message.edit(embed=embed, view=self.plus_view)
        await interaction.response.edit_message(content=f"Участник <@{uid}> удалён из сбора.", view=None)


class RemoveParticipantView(discord.ui.View):
    def __init__(self, plus_view: "PlusView", origin_message: discord.Message, guild: discord.Guild | None):
        super().__init__(timeout=120)
        self.add_item(RemoveParticipantSelect(plus_view, origin_message, guild))


class PlusView(discord.ui.View):
    def __init__(self, max_slots: int, author_id: int, end_time: datetime | None = None):
        super().__init__(timeout=None)
        self.max_slots = max_slots
        self.author_id = author_id
        self.end_time = end_time
        self.message: discord.Message | None = None
        self.participants: list[int] = []
        self.add_participant_button = PlusAddParticipantButton()
        self.delete_button = PlusDeleteParticipantButton()
        self.add_item(self.add_participant_button)
        self.add_item(self.delete_button)

    def is_expired(self) -> bool:
        return self.end_time is not None and datetime.now(timezone.utc) >= self.end_time


def build_leaderboard_embed(page: int) -> tuple[discord.Embed, int]:
    ranking = [item for item in get_ranking() if item[1] > 0]
    total_family = sum(item[1] for item in ranking)
    per_page = 10
    total_pages = max(1, math.ceil(len(ranking) / per_page))
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = ranking[start:start + per_page]

    lines = []
    for idx, (uid, secs) in enumerate(chunk, start=start + 1):
        status = "🟢" if int(uid) in active_sessions else "🔴"
        lines.append(f"{idx}. <@{uid}>  —  {format_duration(secs)}  —  {status}")

    body = "\n".join(lines) if lines else "Пока нет данных."
    description = f"# Онлайн Лидерборд\n{body}\n\nОбщее время Семьи: {format_duration(total_family)}"

    embed = discord.Embed(description=description, color=EMBED_COLOR, timestamp=datetime.now(timezone.utc))
    embed.set_footer(text=FOOTER_TEXT)
    return embed, total_pages


class LeaderboardView(discord.ui.View):
    def __init__(self, page: int = 0):
        super().__init__(timeout=180)
        self.page = page
        self.embed, self.total_pages = build_leaderboard_embed(self.page)
        self.refresh_state()

    def refresh_state(self):
        self.embed, self.total_pages = build_leaderboard_embed(self.page)
        for child in self.children:
            if child.custom_id == "lb_prev":
                child.disabled = self.page <= 0
            elif child.custom_id == "lb_next":
                child.disabled = self.page >= self.total_pages - 1

    @discord.ui.button(label="Страница назад", style=discord.ButtonStyle.secondary, custom_id="lb_prev")
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self.refresh_state()
        await interaction.response.edit_message(embed=self.embed, view=self)

    @discord.ui.button(label="Страница вперёд", style=discord.ButtonStyle.secondary, custom_id="lb_next")
    async def forward(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self.refresh_state()
        await interaction.response.edit_message(embed=self.embed, view=self)


async def respond_personal_activity(interaction: discord.Interaction):
    ranking = [item for item in get_ranking() if item[1] > 0]
    rank = None
    for i, (uid, _) in enumerate(ranking, 1):
        if int(uid) == interaction.user.id:
            rank = i
            break
    secs = total_seconds_for(interaction.user.id)
    place = f"#{rank}" if rank else "—"
    content = (
        f"# <@{interaction.user.id}>\n"
        f"**Место**: {place}\n"
        f"**Проведённое время:** {format_duration(secs)}"
    )
    await interaction.response.send_message(content, ephemeral=True)


async def respond_leaderboard(interaction: discord.Interaction):
    view = LeaderboardView(0)
    await interaction.response.send_message(embed=view.embed, view=view, ephemeral=True)


class ActivityPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Активность", style=discord.ButtonStyle.primary, custom_id="activity_personal_btn")
    async def personal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await respond_personal_activity(interaction)

    @discord.ui.button(label="Лидерборд", style=discord.ButtonStyle.secondary, custom_id="activity_leaderboard_btn")
    async def leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        await respond_leaderboard(interaction)


def activity_panel_embed() -> discord.Embed:
    description = (
        "В этом канале вы можете посмотреть вашу личную статистику о активности в голосовых каналах семьи.\n\n"
        "•ㅤ**Активность**  —  Выводит вашу личную статистику об Активности в Голосовых Каналах.\n"
        "•ㅤ**Лидерборд** —  Выводит рейтинг самых активных людей в Семье по онлайну в Голосовых Каналах.\n\n"
        "Чтобы воспользоваться функционалом воспользуйтесь кнопками ниже:"
    )
    embed = discord.Embed(
        title="Активность в Голосовых Каналах.",
        description=description,
        color=EMBED_COLOR,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=FOOTER_TEXT)
    return embed


# --- COMMANDS ---

@bot.tree.command(name="contract", description="Создает сбор с вашими настройками (Авто-запись)")
@discord.app_commands.describe(
    название="Название сбора",
    дата="Дата и время окончания (ДД.ММ.ГГГГ ЧЧ:ММ)",
    слоты="Максимальное количество участников",
    доп_слоты="Дополнительные участники (необязательно)",
    комментарий="Комментарий к сбору",
    ветка="Название ветки для обсуждения",
    изображение="Изображение для сбора"
)
async def contract_command(interaction: discord.Interaction, название: str, дата: str, слоты: int,
                       доп_слоты: int = 0, комментарий: str = None, ветка: str = None,
                       изображение: discord.Attachment = None):
    if not has_any_role(interaction.user, PLUS_COMMAND_ROLES):
        await interaction.response.send_message("У вас нет прав для использования этой команды.", ephemeral=True)
        return
    if слоты > 100:
        await interaction.response.send_message("Максимальное количество слотов — 100.", ephemeral=True)
        return

    end_time = parse_date_to_utc(дата)
    timer_text = get_time_remaining(end_time) if end_time else ""
    date_value = дата + (f"\n{timer_text}" if timer_text else "")

    embed = discord.Embed(title=название, color=EMBED_COLOR)
    embed.add_field(name="Дата", value=date_value, inline=True)
    embed.add_field(name="Слоты", value=f"{слоты} (+{доп_слоты} доп.)", inline=True)
    embed.add_field(name="Участники", value="Пока никто не записался.", inline=False)
    if комментарий:
        embed.add_field(name="Комментарий", value=комментарий, inline=False)
    embed.set_footer(text=f"{FOOTER_TEXT} · Организатор: {interaction.user.display_name}")
    embed.timestamp = datetime.now(timezone.utc)
    if изображение:
        embed.set_image(url=изображение.url)

    view = ContractView(max_slots=слоты, author_id=interaction.user.id, end_time=end_time)
    view.join_button.label = f"Записаться ({слоты} мест)"

    await interaction.response.send_message(
        content="@everyone", embed=embed, view=view,
        allowed_mentions=discord.AllowedMentions(everyone=True))
    msg = await interaction.original_response()
    view.message = msg
    if end_time is not None:
        active_contract_views.append(view)

    if ветка:
        try:
            thread = await msg.create_thread(name=ветка, auto_archive_duration=1440)
            await thread.send(f"Ветка для обсуждения сбора **{название}** открыта.")
        except Exception as e:
            await interaction.followup.send(f"Не удалось создать ветку: {e}", ephemeral=True)


@bot.tree.command(name="plus", description="Создает сбор с ручным добавлением участников")
@discord.app_commands.describe(
    название="Название сбора",
    дата="Дата и время окончания (ДД.ММ.ГГГГ ЧЧ:ММ)",
    слоты="Максимальное количество участников",
    доп_слоты="Дополнительные участники (необязательно)",
    комментарий="Комментарий к сбору",
    ветка="Название ветки для обсуждения",
    изображение="Изображение для сбора"
)
async def plus_command(interaction: discord.Interaction, название: str, дата: str, слоты: int,
                       доп_слоты: int = 0, комментарий: str = None, ветка: str = None,
                       изображение: discord.Attachment = None):
    if not has_any_role(interaction.user, PLUS_COMMAND_ROLES):
        await interaction.response.send_message("У вас нет прав для использования этой команды.", ephemeral=True)
        return
    if слоты > 100:
        await interaction.response.send_message("Максимальное количество слотов — 100.", ephemeral=True)
        return

    end_time = parse_date_to_utc(дата)
    timer_text = get_time_remaining(end_time) if end_time else ""
    date_value = дата + (f"\n{timer_text}" if timer_text else "")

    embed = discord.Embed(title=название, color=EMBED_COLOR)
    embed.add_field(name="Дата", value=date_value, inline=True)
    embed.add_field(name="Слоты", value=f"{слоты} (+{доп_слоты} доп.)", inline=True)
    embed.add_field(name="Участники", value="Пока никто не записался.", inline=False)
    if комментарий:
        embed.add_field(name="Комментарий", value=комментарий, inline=False)
    embed.set_footer(text=f"{FOOTER_TEXT} · Организатор: {interaction.user.display_name}")
    embed.timestamp = datetime.now(timezone.utc)
    if изображение:
        embed.set_image(url=изображение.url)

    view = PlusView(max_slots=слоты, author_id=interaction.user.id, end_time=end_time)
    
    await interaction.response.send_message(
        content="@everyone", embed=embed, view=view,
        allowed_mentions=discord.AllowedMentions(everyone=True))
    msg = await interaction.original_response()
    view.message = msg
    if end_time is not None:
        active_plus_views.append(view)

    if ветка:
        try:
            thread = await msg.create_thread(name=ветка, auto_archive_duration=1440)
            await thread.send(f"Ветка для обсуждения сбора **{название}** открыта.")
        except Exception as e:
            await interaction.followup.send(f"Не удалось создать ветку: {e}", ephemeral=True)


@bot.tree.command(name="add_payment", description="Добавить человека в список оплаты дома")
@discord.app_commands.describe(пинг="Кого добавить для напоминаний", дата="Дата уведомления (ДД.ММ.ГГГГ)")
async def add_payment(interaction: discord.Interaction, пинг: discord.Member, дата: str):
    if not has_any_role(interaction.user, ADMIN_ROLES):
        await interaction.response.send_message("У вас нет прав для этой команды.", ephemeral=True)
        return
    normalized = validate_date(дата)
    if not normalized:
        await interaction.response.send_message("Неверный формат даты. Используйте ДД.ММ.ГГГГ", ephemeral=True)
        return
    
    payments = load_json(PAYMENTS_FILE, {})
    
    # Проверка: если пользователь уже есть, обновляем дату, иначе добавляем
    user_key = str(пинг.id)
    is_update = user_key in payments
    
    payments[user_key] = {
        "date": normalized,
        "pinged_date": None,
        "channel_ping_date": None
    }
    save_json(PAYMENTS_FILE, payments)
    
    # Отправляем уведомление в канал оплаты
    action = "updated" if is_update else "added"
    await send_payment_notification(пинг.id, action, normalized)
    
    msg_text = f"<@{пинг.id}> добавлен в список оплаты дома на {normalized}."
    if is_update:
        msg_text = f"Дата оплаты для <@{пинг.id}> обновлена на {normalized}."
        
    await interaction.response.send_message(msg_text, ephemeral=True)


@bot.tree.command(name="delete_payment", description="Удалить человека из списка оплаты дома")
@discord.app_commands.describe(пинг="Кого удалить из списка")
async def delete_payment(interaction: discord.Interaction, пинг: discord.Member):
    if not has_any_role(interaction.user, ADMIN_ROLES):
        await interaction.response.send_message("У вас нет прав для этой команды.", ephemeral=True)
        return
    payments = load_json(PAYMENTS_FILE, {})
    if str(пинг.id) not in payments:
        await interaction.response.send_message("Этого человека нет в списке.", ephemeral=True)
        return
    payments.pop(str(пинг.id))
    save_json(PAYMENTS_FILE, payments)
    
    # Отправляем уведомление в канал оплаты
    await send_payment_notification(пинг.id, "removed")
    
    await interaction.response.send_message(f"<@{пинг.id}> удалён из списка оплаты дома.", ephemeral=True)


@bot.tree.command(name="payment", description="Показать список людей для оплаты дома")
async def payment_list(interaction: discord.Interaction):
    payments = load_json(PAYMENTS_FILE, {})
    
    # 1. Проверка на просрочку (> 7 дней) и авто-удаление
    if payments:
        cleaned_payments, removed_info = clean_expired_payments(payments)
        if removed_info:
            save_json(PAYMENTS_FILE, cleaned_payments)
            payments = cleaned_payments 
            # Отправляем уведомления об авто-удалении
            for uid, date_str in removed_info:
                await send_payment_notification(uid, "expired", date_str)

    if not payments:
        embed = discord.Embed(title="Оплата Дома.", description="Список пуст.", color=EMBED_COLOR)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # 2. Сортировка по дате (преобразуем строку даты в объект datetime для сравнения)
    try:
        sorted_payments = sorted(payments.items(), key=lambda item: datetime.strptime(item[1]['date'], "%d.%m.%Y"))
    except ValueError:
        # Если формат даты битый, сортируем как есть
        sorted_payments = list(payments.items())

    lines = []
    for i, (uid, entry) in enumerate(sorted_payments, 1):
        mark = "✅" if entry.get("pinged_date") == entry.get("date") else "❎"
        # Используем <@UID> для упоминания. Если UID неверный, Discord покажет просто текст.
        lines.append(f"{i}.  •  <@{uid}> — {entry.get('date')} — {mark}")
    
    embed = discord.Embed(title="Оплата Дома.", description="\n".join(lines), color=EMBED_COLOR)
    embed.set_footer(text="Сортировка по дате оплаты")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# Команды /leaderbord и /activity видны всем пользователям без ограничений
@bot.tree.command(name="leaderbord", description="Рейтинг активности в голосовых каналах")
async def leaderbord_command(interaction: discord.Interaction):
    await respond_leaderboard(interaction)


@bot.tree.command(name="activity", description="Ваша личная статистика активности")
async def activity_command(interaction: discord.Interaction):
    await respond_personal_activity(interaction)


@bot.tree.command(name="post_activity_panel", description="Опубликовать панель активности в канал (Только для Админов)")
async def post_activity_panel_command(interaction: discord.Interaction):
    if not has_any_role(interaction.user, ADMIN_ROLES):
        await interaction.response.send_message("У вас нет прав для использования этой команды.", ephemeral=True)
        return

    if not ACTIVITY_CHANNEL_ID:
        await interaction.response.send_message("❌ Канал активности не настроен.", ephemeral=True)
        return
    
    channel = bot.get_channel(ACTIVITY_CHANNEL_ID)
    if not channel:
        try:
            channel = await bot.fetch_channel(ACTIVITY_CHANNEL_ID)
        except discord.NotFound:
            await interaction.response.send_message("❌ Канал активности не найден!", ephemeral=True)
            return

    await interaction.response.send_message("📢 Публикую панель активности...", ephemeral=True)
    await channel.send(embed=activity_panel_embed(), view=ActivityPanelView())
    await interaction.followup.send("✅ Панель успешно опубликована.", ephemeral=True)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if bot.user in message.mentions:
        payments = load_json(PAYMENTS_FILE, {})
        key = str(message.author.id)
        if key in payments:
            payments[key]["pinged_date"] = today_msk()
            save_json(PAYMENTS_FILE, payments)
    await bot.process_commands(message)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.bot:
        return
    now = datetime.now(timezone.utc)
    was_in = before.channel is not None
    is_in = after.channel is not None
    if not was_in and is_in:
        active_sessions[member.id] = now
    elif was_in and not is_in:
        start = active_sessions.pop(member.id, None)
        if start is not None:
            elapsed = (now - start).total_seconds()
            voice_totals[str(member.id)] = voice_totals.get(str(member.id), 0) + elapsed
            save_json(VOICE_FILE, voice_totals)


@bot.event
async def on_ready():
    os.system('cls' if os.name == 'nt' else 'clear')
    print(f"✅ Бот успешно запущен: {bot.user}")

    bot.add_view(ActivityPanelView())

    try:
        synced = await bot.tree.sync()
        print(f" Синхронизировано {len(synced)} slash-команд.")
    except Exception as e:
        print(f"❌ Ошибка синхронизации команд: {e}")

    print("-" * 40)

    started = datetime.now(timezone.utc)
    for guild in bot.guilds:
        for channel in guild.voice_channels:
            for member in channel.members:
                if not member.bot and member.id not in active_sessions:
                    active_sessions[member.id] = started

    if not payment_reminder_loop.is_running():
        payment_reminder_loop.start()
        print("🔔 Цикл напоминаний об оплате запущен (12:00, 16:00, 20:00 МСК).")
    if not voice_flush_loop.is_running():
        voice_flush_loop.start()
        print("🎙️ Учёт активности в голосовых каналах активен.")
    if not plus_expiry_loop.is_running():
        plus_expiry_loop.start()
        print("⏰ Цикл проверки завершения сборов запущен.")


bot.run(TOKEN)
