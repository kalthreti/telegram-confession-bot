import json
import os
import logging
from functools import wraps
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Chat, BotCommand
from telegram.ext import (
        Application,
        CommandHandler,
        MessageHandler,
        CallbackQueryHandler,
        ContextTypes,
        filters,
    )
from pytz import utc
from datetime import datetime
from typing import List, Dict, Any, Union

    # Replit specific imports (kept for Replit deployment structure)
from flask import Flask
from threading import Thread

    # --- Flask Keep-Alive Integration (Required for 24/7 uptime on Replit) ---
app = Flask(__name__)


@app.route('/')
def home():
        # This endpoint is constantly pinged by UptimeRobot
        return "Confession Bot is running and awake!"


def run_flask():
        # Run the Flask web server
        port = int(os.environ.get('PORT', 8080))
        # Setting use_reloader=False is crucial when running Flask in a separate thread
        app.run(host='0.0.0.0', port=port, use_reloader=False)


def keep_alive():
        # Start the web server in a separate thread
        t = Thread(target=run_flask)
        t.start()


    # ------------------------------------------------------------------------

    # --- Logging Setup ---
logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO)
logger = logging.getLogger(__name__)

    # ===== HARDCODED CONFIGURATION & ENVIRONMENT VARIABLES (MUST CHANGE!) =====
    # 🛑 IMPORTANT: Replace these placeholder values with your actual data.
BOT_TOKEN = "8394081800:AAHqaAOPyOu1O7xQAJj84JSeh1mCBF0EZlQ"  # Your bot token
CHANNEL_ID = "@thretitest"  # Your public channel username (e.g., @MyConfessionsChannel)
ADMIN_GROUP_ID = -1003301880047  # Your admin group/supergroup ID (starts with -100)

DATA_FILE = "confessions_store.json"
ADMIN_ALIAS = "Admin"
MAX_BATCH_APPROVAL = 15
    # =================================================

    # ===== Persistent Storage (Local JSON) =====
store: dict = {"next_id": 1, "pending": {}, "posted": {}, "user_profiles": {}}


def load_store():
        """Loads state from JSON file on startup."""
        global store
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    loaded_data = json.load(f)
                    store.update(loaded_data)
                    logger.info("Successfully loaded data store.")
            except json.JSONDecodeError:
                logger.warning(
                    f"Could not decode {DATA_FILE}. Starting with fresh store.")
        else:
            logger.info("Data file not found. Initializing new store.")

def save_store():
        """Saves the current state of the bot's store to the JSON file."""
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(store, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save data store: {e}")


    # ===== Access Control Decorator =====
def is_admin_chat(func):
        """Decorator to restrict command access to the specified ADMIN_GROUP_ID."""

        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            chat_id: int = update.effective_chat.id

            if chat_id != ADMIN_GROUP_ID:
                logger.info(
                    f"Access denied for chat ID {chat_id} to command {func.__name__}"
                )
                if update.message:
                    await update.message.reply_text(
                        "🚫 This command can only be used by administrators.")
                return
            return await func(update, context)

        return wrapper


    # ===== Public Interaction Helpers =====
def get_user_alias(user_id: int) -> str:
        """Retrieves the stored nickname or returns a default 'Anonymous'."""
        return store["user_profiles"].get(str(user_id), "Anonymous")


def get_confession_options_text(conf_id: int) -> str:
        """Generates the full text for the main confession view."""
        confession_data = store["posted"].get(str(conf_id))
        if not confession_data:
            return "⚠️ Confession not found."

        conf_alias = confession_data.get("user_alias", "Anonymous")

        text = f"*Confession #{conf_id}* (by {conf_alias})\n\n{confession_data['text']}\n"

        return text


def get_confession_options_markup(conf_id: int) -> InlineKeyboardMarkup:
        """Generates the main keyboard for the initial confession view."""
        keyboard = [[
            InlineKeyboardButton("💬 Add Comment",
                                 callback_data=f"add_comment|{conf_id}"),
            InlineKeyboardButton("👁️ Browse Comments",
                                 callback_data=f"browse_comments|{conf_id}"),
        ]]
        return InlineKeyboardMarkup(keyboard)


async def send_confession_options(update: Update,
                                      context: ContextTypes.DEFAULT_TYPE,
                                      conf_id: int) -> None:
        """Handles sending or editing the message to display the main confession view."""
        text = get_confession_options_text(conf_id)
        markup = get_confession_options_markup(conf_id)

        if update.callback_query and update.callback_query.message:
            await update.callback_query.edit_message_text(text,
                                                          parse_mode="Markdown",
                                                          reply_markup=markup)
        elif update.message:
            await update.message.reply_text(text,
                                            parse_mode="Markdown",
                                            reply_markup=markup)
        else:
            logger.warning(
                "Could not send confession options: Missing update context.")


async def submit_pending_confession(update: Update,
                                        context: ContextTypes.DEFAULT_TYPE,
                                        text: str) -> None:
        """Submits a new, original confession for admin review."""

        conf_id: int = store['next_id']
        pending_id: str = f"p{conf_id}"
        user_id = update.effective_user.id
        user_alias = get_user_alias(user_id)

        store["next_id"] += 1

        store["pending"][pending_id] = {
            "id": conf_id,
            "text": text,
            "from_user": user_id,
            "user_alias": user_alias
        }
        save_store()

        keyboard = [[
            InlineKeyboardButton("✅ Approve Confession",
                                 callback_data=f"approve|{pending_id}"),
            InlineKeyboardButton("❌ Reject Confession",
                                 callback_data=f"reject|{pending_id}"),
        ]]

        try:
            await context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=
                f"🆕 *Pending Confession #{conf_id}* (ID: {pending_id} | Alias: {user_alias})\n\n{text}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            await update.message.reply_text(
                "✅ Your confession has been received and is pending admin approval. You will not receive a further notification if it is posted."
            )
        except Exception as e:
            logger.error(
                f"Failed to send moderation message to admin group {ADMIN_GROUP_ID}: {e}"
            )
            await update.message.reply_text(
                "⚠️ Error: Could not submit to the admin group. Please contact the administrator."
            )
            # Rollback ID and store
            store["next_id"] -= 1
            del store["pending"][pending_id]
            save_store()
            return


    # ===== Core Handlers (Commands and Messages) =====


async def start_command(update: Update,
                            context: ContextTypes.DEFAULT_TYPE) -> None:
        """Greets the user, handles deep links for commenting, and explains the anonymous process (private chat only)."""
        if update.effective_chat.type == Chat.PRIVATE:

            if context.args and context.args[0].startswith("comment_"):
                try:
                    conf_id_str = context.args[0].split('_')[1]
                    conf_id = int(conf_id_str)
                    await send_confession_options(update, context, conf_id)
                    return
                except (IndexError, ValueError) as e:
                    logger.error(f"Error processing deep link: {e}")

            user_alias = get_user_alias(update.effective_user.id)

            welcome_message = (
                f"👋 *Welcome to the weirdo confession bot!* 🤫\n\n"
                f"Your current nickname(Alias) is: *{user_alias}*\n"
                "This bot allows you to share your thoughts, secrets, and stories completely anonymously in our channel (`{CHANNEL_ID}`).\n\n"
                "*Here are the Rules & Guidelines:*\n"
                "1. *Alias:* Use the `/setalias <name>` command to choose a stable nickname for your posts and comments.\n"
                "2. *Anonymity:* Your Telegram user ID is never revealed. Only your chosen alias is displayed.\n"
                "3. *Submission:* Use the `/confess` command, or simply send your message in this chat. It will be sent for review.\n"
                "4. *Review:* All confessions are reviewed by administrators before being posted to the channel.\n"
                "5. *NO Hate Speech:* Submissions must not contain hate speech, bullying, harassment, or discrimination.\n"
                "6. *Respect Rights:* Do not submit content that violates the rights of any individual, including privacy or intellectual property.\n"
                "7. *Interaction:* Use the buttons below posts in the channel to leave anonymous comments and react to other confessions.\n\n"
                "Ready to share? Tap `/confess` or just start typing!")

            await update.message.reply_text(welcome_message, parse_mode="Markdown")


async def set_alias_command(update: Update,
                                context: ContextTypes.DEFAULT_TYPE) -> None:
        """Allows the user to set a persistent nickname (alias)."""
        if update.effective_chat.type != Chat.PRIVATE:
            await update.message.reply_text(
                "This command works only in a private chat with the bot.")
            return

        if not context.args:
            current_alias = get_user_alias(update.effective_user.id)
            await update.message.reply_text(
                f"📝 Your current alias is: *{current_alias}*\n"
                "Usage: `/setalias <new_nickname>`. Your nickname must be between 3 and 20 characters long and contain only letters, numbers, and spaces."
            )
            return

        new_alias = " ".join(context.args).strip()

        if len(new_alias) < 3 or len(new_alias) > 20:
            await update.message.reply_text(
                "❌ Nickname must be between 3 and 20 characters long.")
            return

        if not all(c.isalnum() or c.isspace() for c in new_alias):
            await update.message.reply_text(
                "❌ Nickname can only contain letters, numbers, and spaces.")
            return

        user_id_str = str(update.effective_user.id)
        store["user_profiles"][user_id_str] = new_alias
        save_store()

        await update.message.reply_text(
            f"✅ Your new alias has been set to: *{new_alias}*. This will be used for all future posts and comments.",
            parse_mode="Markdown")


async def help_command(update: Update,
                           context: ContextTypes.DEFAULT_TYPE) -> None:
        """Provides a list of commands and general usage instructions."""
        help_text = (
            "*Welcome to the Confession Bot!* 🤫\n\n"
            "**Public User Commands (Private Chat Only):**\n"
            "1. `/setalias <name>`: Set your persistent nickname/alias.\n"
            "2. `/confess`: Start submitting your anonymous confession.\n"
            "3. `/feedback`: Send anonymous feedback or suggestions directly to the admins.\n"
            "4. `/start`: Get the detailed welcome message and rules.\n"
            "5. `/help`: Show this command summary.\n"
            "6. `/cancel`: Cancel a pending comment or feedback submission.\n\n"
            "**How to Interact:**\n"
            f"Find a confession in the public channel (`{CHANNEL_ID}`) and click the 'Add / View Comments' button to interact."
        )

        if update.effective_chat.id == ADMIN_GROUP_ID:
            help_text += (
                "\n\n*Admin Commands (Admin Chat Only):*\n"
                "1. `/pending`: List all confessions awaiting approval.\n"
                f"2. `/approve_batch [N]`: Approve and post the next N (max {MAX_BATCH_APPROVAL}) pending confessions.\n"
                f"3. `/reply <id> <message>`: Post an auto-approved anonymous comment to confession `<id>` (as `{ADMIN_ALIAS}`).\n"
                "4. `/stats`: Show comment and vote statistics for all posted confessions.\n"
                "5. `/deleteconfession <id>`: Permanently delete a posted confession and all its comments.\n"
                "6. `/deletecomment <id> <index>`: Delete a specific comment by confession `<id>` and comment `<index>` (1-based).\n"
                "7. `/reset_counter`: **DANGER** Clears ALL data and resets the counter to 1."
            )

        await update.message.reply_text(help_text, parse_mode="Markdown")


async def cancel_command(update: Update,
                             context: ContextTypes.DEFAULT_TYPE) -> None:
        """Cancels a pending comment or feedback submission."""
        user_id = update.effective_user.id

        # Check for state stored under user_id key
        state_data = context.user_data.get(user_id, {})
        state = state_data.get('state')

        if state in [
                'awaiting_reply', 'awaiting_feedback', 'awaiting_comment_reply'
        ]:
            del context.user_data[user_id]
            await update.message.reply_text(
                f"❌ Cancelled your {state.split('_')[-1]} submission.")
            return

        await update.message.reply_text("There is no active submission to cancel.")


async def confess_command(update: Update,
                              context: ContextTypes.DEFAULT_TYPE) -> None:
        """Prompts the user to send their confession message."""
        if update.effective_chat.type == Chat.PRIVATE:
            user_alias = get_user_alias(update.effective_user.id)
            await update.message.reply_text(
                f"📝 Please send your anonymous confession message now. Your current alias is *{user_alias}*."
            )
        else:
            await update.message.reply_text(
                "This command works only in a private chat with the bot.")


async def feedback_command(update: Update,
                               context: ContextTypes.DEFAULT_TYPE) -> None:
        """Initiates the feedback submission state for the user."""
        if update.effective_chat.type == Chat.PRIVATE:
            user_id = update.effective_user.id
            context.user_data[user_id] = {'state': 'awaiting_feedback'}
            await update.message.reply_text(
                "📝 You are now submitting *anonymous feedback* to the admins. "
                "Please send your message now.")
        else:
            await update.message.reply_text(
                "This command works only in a private chat with the bot.")


async def handle_confession(update: Update,
                                context: ContextTypes.DEFAULT_TYPE) -> None:
        """Receives a text message from a private chat and handles it as a comment, feedback, or new confession."""
        user_id = update.effective_user.id
        user_alias = get_user_alias(user_id)
        text: str = update.message.text.strip() if update.message.text else ""

        if not text:
            await update.message.reply_text(
                "Please send text only for your submission.")
            return

        # User data is stored under the user_id key in the application context for session management
        user_state = context.user_data.get(user_id)

        # State: Awaiting Reply (to an original Confession)
        if user_state and user_state.get('state') == 'awaiting_reply':
            conf_id_to_reply_to = user_state.get('conf_id')
            del context.user_data[user_id]

            if conf_id_to_reply_to is None:
                await update.message.reply_text(
                    "⚠️ Cannot submit comment: Invalid context.")
                return

            conf_key = str(conf_id_to_reply_to)

            if conf_key not in store["posted"]:
                await update.message.reply_text(
                    "⚠️ Cannot submit comment: The original confession no longer exists."
                )
                return

            reply_id = store['next_id']
            store["next_id"] += 1

            posted_confession = store["posted"][conf_key]
            if "replies" not in posted_confession:
                posted_confession["replies"] = []

            posted_confession["replies"].append({
                "reply_id":
                reply_id,
                "text":
                text,
                "user_alias":
                user_alias,
                "approved_time":
                update.message.date.astimezone(utc).isoformat(),
                "voters": {}
            })
            save_store()

            await update.message.reply_text(
                f"✅ Your comment to Confession #{conf_id_to_reply_to} (as *{user_alias}*) has been posted ",
                parse_mode="Markdown")
            return

        # State: Awaiting Comment Reply (to an existing Comment) - NEW FEATURE
        if user_state and user_state.get('state') == 'awaiting_comment_reply':
            target_conf_id = user_state.get('conf_id')
            target_reply_id = user_state.get('reply_id')
            del context.user_data[user_id]

            if target_conf_id is None or target_reply_id is None:
                await update.message.reply_text(
                    "⚠️ Cannot submit comment reply: Invalid context.")
                return

            conf_key = str(target_conf_id)
            if conf_key not in store["posted"]:
                await update.message.reply_text(
                    "⚠️ The original confession no longer exists.")
                return

            # Find the target comment index
            replies = store["posted"][conf_key]["replies"]
            target_comment_index = -1
            for i, reply in enumerate(replies):
                if reply.get('reply_id') == target_reply_id:
                    target_comment_index = i
                    break

            if target_comment_index == -1:
                await update.message.reply_text(
                    "⚠️ The comment you are replying to no longer exists.")
                return

            # Prepend reply text to distinguish it
            replying_to_alias = replies[target_comment_index].get(
                'user_alias', 'Anonymous')
            reply_text_full = f"*Reply to {replying_to_alias}:* {text}"

            # Submit the reply as a new comment
            new_reply_id = store['next_id']
            store["next_id"] += 1

            replies.append({
                "reply_id":
                new_reply_id,
                "text":
                reply_text_full,
                "user_alias":
                user_alias,
                "approved_time":
                update.message.date.astimezone(utc).isoformat(),
                "voters": {}
            })
            save_store()

            await update.message.reply_text(
                f"✅ Your reply (as *{user_alias}*) to Comment ID {target_reply_id} on Confession #{target_conf_id} has been posted.",
                parse_mode="Markdown")
            return

        # State: Awaiting Feedback
        if user_state and user_state.get('state') == 'awaiting_feedback':
            del context.user_data[user_id]

            try:
                await context.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=f"✉️ *New Anonymous Feedback*\n\n{text}",
                    parse_mode="Markdown",
                )
                await update.message.reply_text(
                    "✅ Your feedback has been sent to the administrators. Thank you!"
                )
            except Exception as e:
                logger.error(
                    f"Failed to send feedback to admin group {ADMIN_GROUP_ID}: {e}"
                )
                await update.message.reply_text(
                    "⚠️ Error: Could not send feedback to the admin group. Please try again later."
                )
            return

        # Default: Treat message as a new confession
        await submit_pending_confession(update, context, text)


async def handle_callbacks(update: Update,
                               context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handles inline button presses."""
        query = update.callback_query
        await query.answer()

        action_data = query.data.split("|")

        # --- 1. Public Interaction Callbacks ---

        # Handle Comment Voting & Replying
        if action_data[0] in ["vote_comment", "reply_comment"]:
            if len(action_data) < 3: return
            action, conf_id_str, reply_id_str, *vote_type_list = action_data

            user_id = str(update.effective_user.id)

            try:
                conf_id = int(conf_id_str)
                reply_id = int(reply_id_str)
            except ValueError:
                await query.answer("Invalid Confession or Reply ID.")
                return

            conf_key = str(conf_id)
            if conf_key not in store["posted"]:
                await query.answer("Confession not found.")
                return

            replies = store["posted"][conf_key].get("replies", [])

            target_reply = None
            comment_index = -1
            for i, r in enumerate(replies):
                if r.get('reply_id') == reply_id:
                    target_reply = r
                    comment_index = i + 1
                    break

            if not target_reply:
                await query.answer("Comment not found.")
                return

            if action == "reply_comment":
                user_alias = get_user_alias(int(user_id))

                # Store state under user_id key
                context.user_data[int(user_id)] = {
                    'state': 'awaiting_comment_reply',
                    'conf_id': conf_id,
                    'reply_id': reply_id
                }

                comment_author_alias = target_reply.get('user_alias', 'Anonymous')

                response_text = (
                    f"**You are submitting a REPLY to Comment {comment_index}** (by *{comment_author_alias}*)\n\n"
                    f"Your alias for this reply will be: *{user_alias}*\n\n"
                    f"Please send your reply message now.")
                await query.edit_message_text(response_text, parse_mode="Markdown")
                return

            # Handle Voting Logic
            current_vote = vote_type_list[0]
            voters = target_reply.get('voters', {})
            previous_vote = voters.get(user_id)

            if previous_vote == current_vote:
                # Toggle off vote if it's the same
                del voters[user_id]
                message_text = f"Vote removed!"
            elif previous_vote and previous_vote != current_vote:
                 # Change vote
                voters[user_id] = current_vote
                message_text = f"Vote changed to '{current_vote}'!"
            else:
                # Set new vote
                voters[user_id] = current_vote
                message_text = f"{current_vote.capitalize()} counted!"

            target_reply['voters'] = voters
            save_store()

            # Re-render the comment keyboard
            likes = sum(1 for vote in voters.values() if vote == 'like')
            dislikes = sum(1 for vote in voters.values() if vote == 'dislike')

            updated_keyboard = [
                [
                    InlineKeyboardButton(
                        f"👍 Like ({likes})",
                        callback_data=f"vote_comment|{conf_id}|{reply_id}|like"),
                    InlineKeyboardButton(
                        f"👎 Dislike ({dislikes})",
                        callback_data=f"vote_comment|{conf_id}|{reply_id}|dislike"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "↩️ Reply to Comment",
                        callback_data=f"reply_comment|{conf_id}|{reply_id}"),
                ]
            ]
            updated_markup = InlineKeyboardMarkup(updated_keyboard)

            comment_author_alias = target_reply.get('user_alias', 'Anonymous')
            comment_text = f"*Comment {comment_index}* (by {comment_author_alias} | ID: {reply_id}):\n\n{target_reply['text']}"

            if query.message.text and query.message.text.startswith("*Comment"):
                await query.edit_message_text(comment_text,
                                              parse_mode="Markdown",
                                              reply_markup=updated_markup)

            await query.answer(message_text)
            return

        # Handle View Confession / Add Comment (Logic remains mostly the same)
        if action_data[0] in ["view_confession", "add_comment"]:
            action, data_id = action_data[0], action_data[1]
            try:
                conf_id = int(data_id)
            except (IndexError, ValueError):
                await query.edit_message_text("Invalid confession ID.")
                return

            confession_data = store["posted"].get(str(conf_id))

            if not confession_data:
                await query.edit_message_text("⚠️ Confession not found.")
                return

            if action == "view_confession":
                await send_confession_options(update, context, conf_id)
                return

            elif action == "add_comment":
                user_id = update.effective_user.id
                user_alias = get_user_alias(user_id)

                context.user_data[user_id] = {
                    'state': 'awaiting_reply',
                    'conf_id': conf_id
                }

                response_text = (
                    f"** You are submitting a comment to Confession #{conf_id}** (as *{user_alias}*)\n\n"
                    f" \n> {confession_data['text']}\n\n"
                    f"Please send your comment ")
                await query.edit_message_text(response_text, parse_mode="Markdown")
                return

        elif action_data[0] == "browse_comments":
            _, data_id = action_data
            user_chat_id = query.message.chat.id

            try:
                conf_id = int(data_id)
            except ValueError:
                await query.edit_message_text("Invalid confession ID.")
                return

            confession_data = store["posted"].get(str(conf_id))

            if not confession_data:
                await query.edit_message_text("⚠️ Confession not found.")
                return

            replies = confession_data.get("replies", [])

            # Edit the current message to be the header
            await query.edit_message_text(
                f"📝 *Displaying {len(replies)} Comments for Confession #{conf_id}*",
                parse_mode="Markdown")

            channel_message_id = confession_data.get("channel_message_id")

            if channel_message_id:
                channel_username = CHANNEL_ID.lstrip('@')
                back_button = InlineKeyboardButton(
                    "📢 View Confession in Channel",
                    url=f"https://t.me/{channel_username}/{channel_message_id}")
                final_text = "*_End of Comments List. Click the button below to go directly to the original Confession post._*"
            else:
                back_button = InlineKeyboardButton(
                    "⬅️ Back to Confession Options (Private)",
                    callback_data=f"view_confession|{conf_id}")
                final_text = "*_End of Comments List. Click 'Back to Confession Options (Private)' to return to the main post options._*"

            add_comment_button = InlineKeyboardButton(
                "💬 Add New Comment", callback_data=f"add_comment|{conf_id}")

            navigation_keyboard = [[back_button, add_comment_button]]
            final_markup = InlineKeyboardMarkup(navigation_keyboard)

            # Send each comment as a separate message
            if replies:
                for i, reply in enumerate(replies, 1):
                    reply_id = reply.get("reply_id", int(data_id) * 1000 + i)

                    voters = reply.get('voters', {})
                    likes = sum(1 for vote in voters.values() if vote == 'like')
                    dislikes = sum(1 for vote in voters.values()
                                   if vote == 'dislike')

                    comment_author_alias = reply.get('user_alias', 'Anonymous')

                    comment_text = f"*Comment {i}* (by {comment_author_alias} | ID: {reply_id}):\n\n{reply['text']}"

                    comment_keyboard = [
                        [
                            InlineKeyboardButton(
                                f"👍 Like ({likes})",
                                callback_data=
                                f"vote_comment|{conf_id}|{reply_id}|like"),
                            InlineKeyboardButton(
                                f"👎 Dislike ({dislikes})",
                                callback_data=
                                f"vote_comment|{conf_id}|{reply_id}|dislike"),
                        ],
                        [
                            InlineKeyboardButton(
                                "↩️ Reply to Comment",
                                callback_data=f"reply_comment|{conf_id}|{reply_id}"
                            ),
                        ]
                    ]

                    await context.bot.send_message(
                        chat_id=user_chat_id,
                        text=comment_text,
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(comment_keyboard))

                # Send final navigation message
                await context.bot.send_message(chat_id=user_chat_id,
                                               text=final_text,
                                               parse_mode="Markdown",
                                               reply_markup=final_markup)

            else:
                # Send the 'No comments yet' message with the navigation buttons
                response_text = f"📝 *Comments for Confession #{conf_id}*\n\n"
                response_text += "_No comments yet. Be the first one to share your thoughts._\n\n"

                # Use the original message to send the full text
                await query.edit_message_text(
                    text=response_text,
                    parse_mode="Markdown",
                    reply_markup=final_markup
                )

            return

        # --- 2. Admin Action Block ---

        if query.message.chat.id != ADMIN_GROUP_ID:
            return

        # --- Handle Confession Approval/Rejection (Single Item) ---
        if action_data[0] in ["approve", "reject"]:
            if len(action_data) != 2: return
            action, data_id = action_data

            if data_id not in store["pending"]:
                await query.edit_message_text(query.message.text +
                                              "\n\n⚠️ *Already Handled*",
                                              parse_mode="Markdown")
                return

            pending_item = store["pending"][data_id]
            conf_id = pending_item["id"]
            conf_alias = pending_item["user_alias"]

            success_text = query.message.text + f"\n\n✅ *Approved by {update.effective_user.first_name}*"

            if action == "reject":
                del store["pending"][data_id]
                save_store()
                await query.edit_message_text(
                    query.message.text +
                    f"\n\n❌ *Rejected by {update.effective_user.first_name}*",
                    parse_mode="Markdown")

            elif action == "approve":
                conf = store["pending"].pop(data_id)

                temp_conf_data = {
                    "text":
                    conf["text"],
                    "user_alias":
                    conf_alias,
                    "post_time":
                    update.callback_query.message.date.astimezone(utc).isoformat(),
                    "replies": [],
                    "channel_message_id":
                    None
                }
                store["posted"][str(conf_id)] = temp_conf_data
                save_store()

                bot_info = await context.bot.get_me()
                bot_username = bot_info.username

                reply_keyboard = [[
                    InlineKeyboardButton(
                        "💬 Add / View Comments",
                        url=f"https://t.me/{bot_username}?start=comment_{conf_id}")
                ]]
                post_text = f"*#{conf_id} Confession* - Posted by: {conf_alias}\n\n{conf['text']}"
                try:
                    sent_message = await context.bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=post_text,
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(reply_keyboard))

                    store["posted"][str(
                        conf_id)]["channel_message_id"] = sent_message.message_id
                    save_store()

                    await query.edit_message_text(success_text +
                                                  f"\n\n🔗 [View Post in Channel]({sent_message.link})",
                                                  parse_mode="Markdown")

                except Exception as e:
                    logger.error(
                        f"Failed to post Confession #{conf_id} to channel {CHANNEL_ID}: {e}"
                    )
                    # Revert the approval in case of posting failure
                    del store["posted"][str(conf_id)]
                    store["pending"][data_id] = conf
                    save_store()
                    await query.edit_message_text(
                        query.message.text +
                        f"\n\n❌ *Approval Failed: Could not post to channel. Reverted.* Error: {e}",
                        parse_mode="Markdown")
            return


    # ===== Admin Commands Implementation =====


@is_admin_chat
async def pending_command(update: Update,
                              context: ContextTypes.DEFAULT_TYPE) -> None:
        """Lists all pending confessions for the admins."""
        pending_list = store["pending"].values()
        pending_count = len(pending_list)

        if pending_count == 0:
            await update.message.reply_text("✅ No confessions are currently pending review.")
            return

        message = f"📋 *{pending_count} Confessions Awaiting Review:*\n\n"

        # Sort by ID for consistent ordering
        sorted_pending = sorted(pending_list, key=lambda x: x['id'])

        for i, conf in enumerate(sorted_pending):
            conf_id = conf['id']
            pending_id = f"p{conf_id}"
            alias = conf['user_alias']

            # Truncate text for summary view
            text_summary = conf['text'][:50].replace('\n', ' ')
            if len(conf['text']) > 50:
                text_summary += "..."

            message += f"*{i+1}.* ID: `{pending_id}` (Alias: {alias})\n"
            message += f"   Text: _{text_summary}_\n\n"

            # Telegram message limit is ~4096 characters. Stop if message gets too long.
            if len(message) > 3500:
                message += f"... and {pending_count - i - 1} more confessions. Use /approve_batch to process them quickly."
                break

        await update.message.reply_text(message, parse_mode="Markdown")


@is_admin_chat
async def approve_batch_command(update: Update,
                                    context: ContextTypes.DEFAULT_TYPE) -> None:
        """Approves and posts a batch of pending confessions to the channel."""

        pending_items: List[Dict[str, Any]] = list(store["pending"].values())
        pending_count = len(pending_items)

        if pending_count == 0:
            await update.message.reply_text("✅ No confessions are currently pending review.")
            return

        # Determine batch size
        batch_size = MAX_BATCH_APPROVAL
        if context.args:
            try:
                requested_size = int(context.args[0])
                batch_size = min(requested_size, MAX_BATCH_APPROVAL)
            except ValueError:
                pass # Use default batch size if argument is invalid

        batch_to_approve = sorted(pending_items, key=lambda x: x['id'])[:batch_size]

        approved_count = 0
        failed_ids = []

        await update.message.reply_text(f"🚀 Attempting to approve and post *{len(batch_to_approve)}* confessions...", parse_mode="Markdown")

        # Prep for batch posting
        bot_info = await context.bot.get_me()
        bot_username = bot_info.username
        admin_name = update.effective_user.first_name

        for conf in batch_to_approve:
            conf_id = conf['id']
            pending_id = f"p{conf_id}"

            # Remove from pending store
            if pending_id in store["pending"]:
                del store["pending"][pending_id]
            else:
                # Should not happen in a batch loop, but as a guard
                continue 

            temp_conf_data = {
                "text": conf["text"],
                "user_alias": conf["user_alias"],
                "post_time": datetime.now(utc).isoformat(),
                "replies": [],
                "channel_message_id": None
            }
            store["posted"][str(conf_id)] = temp_conf_data

            reply_keyboard = [[
                InlineKeyboardButton(
                    "💬 Add / View Comments",
                    url=f"https://t.me/{bot_username}?start=comment_{conf_id}")
            ]]
            post_text = f"*#{conf_id} Confession* - Posted by: {conf['user_alias']}\n\n{conf['text']}"

            try:
                sent_message = await context.bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=post_text,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(reply_keyboard))

                # Update posted data with channel message ID
                store["posted"][str(conf_id)]["channel_message_id"] = sent_message.message_id
                approved_count += 1

            except Exception as e:
                logger.error(f"Failed to post Confession #{conf_id} during batch: {e}")
                failed_ids.append(conf_id)
                # Revert the item back to pending since posting failed
                store["pending"][pending_id] = conf
                del store["posted"][str(conf_id)] # Remove failed post from 'posted'

        save_store()

        # Final Summary Message
        summary_message = f"✅ *Batch Approval Complete (by {admin_name})*\n"
        summary_message += f"Posted successfully: *{approved_count}* confession(s).\n"
        summary_message += f"Remaining pending: *{len(store['pending'])}*.\n"

        if failed_ids:
            summary_message += f"⚠️ *Failed to Post:* {', '.join(map(str, failed_ids))} (Check bot permissions).\n"

        await update.message.reply_text(summary_message, parse_mode="Markdown")


@is_admin_chat
async def reply_command(update: Update,
                            context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin command to post an instant, anonymous comment (as 'Admin')."""

        if len(context.args) < 2:
            await update.message.reply_text(
                f"Usage: `/reply <confession_id> <message>`. Posts a comment as *{ADMIN_ALIAS}*.",
                parse_mode="Markdown")
            return

        try:
            conf_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid confession ID format.")
            return

        conf_key = str(conf_id)
        if conf_key not in store["posted"]:
            await update.message.reply_text(
                f"❌ Confession #{conf_id} not found in posted confessions.")
            return

        comment_text = " ".join(context.args[1:])

        reply_id = store['next_id']
        store["next_id"] += 1

        posted_confession = store["posted"][conf_key]
        if "replies" not in posted_confession:
            posted_confession["replies"] = []

        posted_confession["replies"].append({
            "reply_id": reply_id,
            "text": comment_text,
            "user_alias": ADMIN_ALIAS,  # Post as Admin
            "approved_time": datetime.now(utc).isoformat(),
            "voters": {}
        })
        save_store()

        await update.message.reply_text(
            f"✅ Admin comment posted to Confession #{conf_id} (ID: {reply_id}).\n\nComment: _{comment_text}_",
            parse_mode="Markdown")


    # --- Placeholder Admin Commands (To be implemented similarly) ---
@is_admin_chat
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("📊 Statistics command is not yet fully implemented. It will show comment/vote counts.")

@is_admin_chat
async def delete_confession_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("🗑️ Delete Confession command is not yet implemented.")

@is_admin_chat
async def delete_comment_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("🗑️ Delete Comment command is not yet implemented.")

@is_admin_chat
async def reset_counter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("⚠️ Danger! Reset Counter command is not yet implemented.")


    # ===== Main Application Setup =====
def main() -> None:
        """Start the bot."""

        if BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
            logger.error("🚫 BOT_TOKEN is not set. Please replace the placeholder value.")
            return

        # Load persistent data first
        load_store()

        # Create the Application and pass it your bot's token.
        application = Application.builder().token(BOT_TOKEN).build()

        # --- Public Handlers ---
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("setalias", set_alias_command))
        application.add_handler(CommandHandler("confess", confess_command))
        application.add_handler(CommandHandler("feedback", feedback_command))
        application.add_handler(CommandHandler("cancel", cancel_command))

        # --- Admin Handlers ---
        application.add_handler(CommandHandler("pending", pending_command))
        application.add_handler(CommandHandler("approve_batch", approve_batch_command))
        application.add_handler(CommandHandler("reply", reply_command))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(CommandHandler("deleteconfession", delete_confession_command))
        application.add_handler(CommandHandler("deletecomment", delete_comment_command))
        application.add_handler(CommandHandler("reset_counter", reset_counter_command))

        # --- Generic Handlers (Must come after Command Handlers) ---
        # Handle incoming messages that are not commands in private chat
        application.add_handler(
            MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, handle_confession)
        )
        # Handle all inline button callbacks
        application.add_handler(CallbackQueryHandler(handle_callbacks))

        # Add command list to bot for user convenience
        commands = [
            BotCommand("start", "Get started and see the rules"),
            BotCommand("setalias", "Set your anonymous nickname"),
            BotCommand("confess", "Submit an anonymous confession"),
            BotCommand("feedback", "Send anonymous feedback to admins"),
            BotCommand("cancel", "Cancel current comment/feedback submission"),
        ]

        # Run a separate thread for the Flask keep-alive server
        keep_alive()

        # Start the Bot
        logger.info("Bot is running...")
        application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
        main()
