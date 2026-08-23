# Apna Free Fire Bot

A Telegram bot for Free Fire player information, Like APIs, profile formatting, AutoLike subscriptions, and Force-to-Join access control.

## Features

- Free Fire player profile lookup with /Get <uid>
- Premium Unicode small-caps profile output
- Multiple Like API support with configurable routing
- /like <region> <uid> command
- Force-to-Join verification for channels, groups, and supergroups
- Admin-only Force-to-Join setup with /addforce <chat_id>
- Banner, outfit, and profile media support
- VIP AutoLike subscription management
- Daily Like limits and admin/unlimited-user support
- MongoDB-backed user, group, API, AutoLike, and Force-to-Join data
- Health server and persistent Telegram reconnect handling

## Commands

### User commands

~~~text
/Get <uid>
/like <region> <uid>
/start
~~~

Regular users must pass all configured Force-to-Join checks before /Get or /like processing. Regular users cannot run these commands from private chat after passing Force-to-Join; they should use the bot in a group.

### Admin commands

~~~text
/addforce <chat_id>
/addvip <region> <uid> <days>
/setapi 1 | 2 | 3 | all
/addlikeapi <https-url>
/likeapis
/removelikeapi <number>
/removealllikeapis
/users
~~~

Admin commands are restricted to the configured admin ID.

## Force-to-Join setup

1. Add the bot to the target channel, group, or supergroup.
2. Promote the bot to administrator.
3. Run this command from the bot admin account:

~~~text
/addforce -1001234567890
~~~

The bot validates the chat, detects its type, obtains a public or private invite link, and stores the configuration in MongoDB. Users who have not joined all configured chats receive Join Now and Check buttons.

Telegram Bot API cannot promote a bot automatically. The bot must be manually added and promoted by a human administrator.

## Database

The bot uses MongoDB with these collections:

- users — Telegram users, Like limits, usage counters, and VIP status
- groups — group metadata and administrator information
- apis — Like API routing and catalog configuration
- autolike — VIP AutoLike subscriptions and expiry data
- force_join — required Force-to-Join chats and invite links

Unique indexes protect user IDs, group chat IDs, AutoLike UID-region pairs, and Force-to-Join chat IDs. Expired AutoLike records are removed using a MongoDB TTL index.

## Configuration

Create or update config.json with the required values:

~~~json
{
  "api_id": 123456,
  "api_hash": "your_api_hash",
  "bot_token": "your_bot_token",
  "admin_id": 123456789,
  "mongodb_uri": "mongodb+srv://...",
  "database_name": "apna"
}
~~~

Never commit real bot tokens, API keys, or MongoDB credentials. Keep them in a secure secrets manager or private deployment configuration.

## Run locally

Install dependencies:

~~~bash
pip install -r requirements.txt
~~~

Start the bot:

~~~bash
python bot.py
~~~

The bot initializes MongoDB indexes during startup and keeps the Telegram connection alive after transient failures.

## Project structure

~~~text
bot.py           Main Telegram bot and database logic
config.json      Runtime configuration template
requirements.txt Python dependencies
Procfile         Deployment process definition
Downloads/       Temporary downloaded media files
~~~

## Notes

- Player information, banner, outfit, and Like requests use configured external APIs.
- Missing profile fields are displayed as Nᴏᴛ Aᴠᴀɪʟᴀʙʟᴇ.
- Raw skill, outfit, weapon-skin, pet, and badge IDs are not exposed in profile output.
- Force-to-Join verification failures are handled without crashing the bot.