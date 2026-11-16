"""Reusable Discord UI views.

Every view and modal in the bot derives from BaseView / BaseModal so that a crash inside a button
handler is logged with context and the player is told something went wrong, instead of the
button silently doing nothing.
"""
import functools
import logging

import discord

from utils.economy import refund_unposted

log = logging.getLogger("gamblebot.ui")


def describe(interaction: discord.Interaction) -> str:
    user = interaction.user
    return f"{user} ({user.id})" if user else "unknown user"


async def tell_user_it_failed(interaction: discord.Interaction) -> None:
    msg = "❌ Something went wrong with that button. The error was logged; please tell an admin."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass


class BaseView(discord.ui.View):
    """A view that logs errors from button callbacks and timeouts."""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # on_timeout runs in a background task, so an exception there would otherwise vanish.
        if "on_timeout" in cls.__dict__:
            original = cls.__dict__["on_timeout"]

            @functools.wraps(original)
            async def on_timeout(self):
                log.debug("%s timed out", type(self).__name__)
                try:
                    await original(self)
                except Exception:
                    log.exception("Error in %s.on_timeout", type(self).__name__)

            cls.on_timeout = on_timeout

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        label = getattr(item, "label", None) or getattr(item, "custom_id", None) or type(item).__name__
        log.error("Error in %s, button %r, pressed by %s", type(self).__name__, label, describe(interaction),
                  exc_info=error)
        await refund_unposted(interaction)
        await tell_user_it_failed(interaction)


class BaseModal(discord.ui.Modal):
    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.error("Error in %s, submitted by %s", type(self).__name__, describe(interaction), exc_info=error)
        await refund_unposted(interaction)
        await tell_user_it_failed(interaction)


class OwnedView(BaseView):
    """A view whose buttons only the owner may press."""

    def __init__(self, owner_id: int, timeout: float | None = 120):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.message: discord.Message | discord.InteractionMessage | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This isn't your game!", ephemeral=True)
            return False
        return True

    def disable_all(self) -> None:
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True


class ConfirmView(OwnedView):
    def __init__(self, owner_id: int, timeout: float = 30):
        super().__init__(owner_id, timeout=timeout)
        self.value: bool | None = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger, emoji="✔️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self.disable_all()
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.disable_all()
        await interaction.response.edit_message(view=self)
        self.stop()
