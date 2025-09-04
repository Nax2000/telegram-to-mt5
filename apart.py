from telethon import TelegramClient

api_id = 20009276       # tu api_id de my.telegram.org
api_hash = 'c384b26a224da864fc4a72b4a813c159' # tu api_hash

client = TelegramClient('session', api_id, api_hash)

async def main():
    async for dialog in client.iter_dialogs():
        print(dialog.name, '→', dialog.id)

with client:
    client.loop.run_until_complete(main())
