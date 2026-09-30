"""Future async repositories and migrations. No database is needed for the foundation.

Open a pool in KYBot.setup_hook with bot.resources.enter_async_context(...).
Inject repositories into services, and services into cogs. Do not perform blocking
database work in Discord event handlers. Scope server settings by guild ID.
"""
