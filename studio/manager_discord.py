"""Discord transport: owner-only input, durable inbox, retryable outbox."""
import asyncio
import datetime
import json
import logging
from pathlib import Path
import time

import fe_board
import manager_brief as brief
import manager_outlook as outlook
import manager_quick as quick
import manager_store as store
import manager_talk as talk
import studio_config

LOG = logging.getLogger(__name__)
MAX_ATTACHMENTS = 10  # Discord's limit for one message (error 50035 "Must be 10 or fewer")


def authorized(config, user, guild, channel, parent=None, known_threads=()):
    return (str(user) in config.get('owner_ids', [config['owner_id']]) and str(guild) == config['guild_id']
            and (str(channel) == config['channel_id'] or
                 str(parent) == config['channel_id'] and str(channel) in known_threads))


def refusal(name, content_type, size):
    """Why a Discord attachment is not kept, known before it is downloaded; None to try it."""
    if (content_type or '').startswith('image/'):
        limit = fe_board.MAX_IMAGE
    elif Path(name).suffix.lstrip('.').lower() in fe_board.FILE_KINDS:
        limit = fe_board.MAX_FILE
    else:
        return 'not a kind I keep: images, ' + ', '.join(sorted(fe_board.FILE_KINDS))
    return f'over {limit >> 20} MB' if size > limit else None


def attachment_mark(data, name, content_type):
    """What a kept attachment adds to the message: an image's markdown, or a file's line,
    `[file NAME] PATH`, the board's copy a planner or worker can take it from."""
    if (content_type or '').startswith('image/'):
        return fe_board.store_image(data, name)
    return f'[file {name}] {fe_board.store_file(data, name)}'


def skipped_note(skipped, passed_on):
    return ("I couldn't take " + '; '.join(skipped) + '. '
            + ('The rest of your message went through.' if passed_on
               else 'Nothing else came with it, so nothing was passed on: send it again as a kind I keep, '
                    'or put it in the checkout and say where.'))


def run_thread(b, r):
    """The thread a run's live line goes in: its task's, its goal's planning thread, or the
    talk channel (D317)."""
    if r['role'] == 'quick':
        return quick.thread_of_run(b, r)
    if r['task_id']:
        x = b.q1('SELECT channel_id FROM pm_discord_threads WHERE task_id=?', r['task_id'])
        return x[0] if x else None
    return talk.thread_of(b, r['goal_id']) if r['goal_id'] and r['role'] == 'planner' else None


def progress_text(b, r):
    """A run's live line (D226): how long, on what model, its latest words and steps."""
    import manager_models as models
    import manager_workers as workers
    started = r['pid_started'] or r['created']
    s = max(0, time.time() - started)
    took = f'{int(s // 60)}m' if s < 3600 else f'{int(s // 3600)}h {int(s % 3600 // 60)}m'
    st = models.runs_stats(b, [r['id']]).get(r['id']) or {}
    model, tier = st.get('model') or '', st.get('tier') or ''
    # Until its stream names the model, a run on a tier's alias ('sonnet') shows the model
    # that tier last ran (D300), so the live line says "Sonnet 5.5", not the class alone.
    label = models.tier_label(b, tier) if tier and model in ('', tier) else models.label(model or tier or r['provider'])
    who = {'planner': 'Planning', 'quick': 'Answering'}.get(r['role'], 'Working')
    lines = [f'⏳ **{who}** · {took} · {label}']
    words = workers.last_words(r['id'])
    if words:
        lines.append('> ' + words[:300])
    for step in workers.last_activity(r['id'], 4):
        if step.startswith('ran: '):
            lines.append('-# ' + step[5:][:160])
    return '\n'.join(lines)[:1800]


def build_client(config, wake):
    import discord
    from discord import app_commands

    class Client(discord.Client):
        def __init__(self):
            intents = discord.Intents.default()
            intents.message_content = True
            super().__init__(intents=intents)
            self.tree = app_commands.CommandTree(self)
            self.synced = False
            self.delivery_task = None
            self.fatal_error = None

        def allowed(self, user, guild, channel):
            with fe_board.Board() as b:
                threads = [r[0] for r in b.q('SELECT channel_id FROM pm_discord_threads')]
                threads += [r[0] for r in b.q('SELECT channel_id FROM pm_goal_threads')]  # D226
            return authorized(config, user.id, getattr(guild, 'id', None), channel.id,
                              getattr(channel, 'parent_id', None), threads)

        def task_for(self, channel):
            with fe_board.Board() as b:
                r = b.q1('SELECT task_id FROM pm_discord_threads WHERE channel_id=?', str(channel.id))
            return r[0] if r else None

        def goal_for(self, channel):
            with fe_board.Board() as b:
                return talk.goal_for_channel(b, channel.id)

        async def find_talk(self, root, required):
            """The talk channel (D317): the configured talk_channel_id, else the server's text
            channel named meatbag-talk. Found, it is remembered for the core and the outbox, and
            only what is said there from now on is answered."""
            cid = str(config.get('talk_channel_id') or '')
            try:
                found = (self.get_channel(int(cid)) or await self.fetch_channel(int(cid))) if cid else next(
                    (c for c in root.guild.text_channels
                     if quick.channel_name(c.name) == quick.channel_name(config.get('talk_channel') or quick.CHANNEL)),
                    None)
            except discord.HTTPException:
                LOG.exception('Could not open the talk channel %s', cid)
                found = None
            if found is not None:
                missing = [p for p in required if p not in ('send_messages_in_threads', 'create_public_threads')
                           and not getattr(found.permissions_for(root.guild.me), p)]
                if missing:
                    LOG.warning('Bot is missing talk channel permissions: %s', ', '.join(missing))
                    found = None
            with fe_board.Board() as b, b.tx():
                if found is None:
                    b.con.execute('DELETE FROM pm_settings WHERE key=?', (quick.SETTING,))
                    return None
                store.set_setting(b, quick.SETTING, str(found.id))
                key = 'discord_cursor:' + str(found.id)
                if not store.setting(b, key):
                    store.set_setting(b, key, discord.utils.time_snowflake(datetime.datetime.now(datetime.timezone.utc)))
                store.notify(b, 'quick-onboarding', 'Ask me anything here while you review: what a task changed, '
                             'why, where its evidence is, what waits on what. I answer in a minute or so and change '
                             'nothing; a change still goes in the task\'s thread.')
            return found

        async def on_ready(self):
            LOG.info('Discord connected as %s', self.user.id)
            root = self.get_channel(int(config['channel_id'])) or await self.fetch_channel(int(config['channel_id']))
            if not isinstance(root, discord.TextChannel) or str(root.guild.id) != config['guild_id']:
                raise ValueError('Configured project channel must be a text channel in the configured server')
            permissions = root.permissions_for(root.guild.me)
            required = ('view_channel', 'send_messages', 'send_messages_in_threads', 'create_public_threads',
                        'read_message_history', 'embed_links', 'attach_files')
            missing = [p for p in required if not getattr(permissions, p)]
            if missing:
                raise ValueError('Bot is missing project channel permissions: ' + ', '.join(missing))
            if not self.synced:
                await self.tree.sync(guild=discord.Object(id=int(config['guild_id'])))
                self.synced = True
            await self.find_talk(root, required)
            with fe_board.Board() as b:
                store.notify(b, 'manager-onboarding-plan', f'{studio_config.name()} Manager is connected. Write what you '
                             'want here: each message opens a planning thread, a conversation like a Claude Code '
                             'session in plan mode, ending in a plan you approve with one button. Tasks get their '
                             'own threads, with a live line while they work. Use /status, /models, /pause, '
                             '/resume or /stop.')
            if self.delivery_task is None or self.delivery_task.done():
                self.delivery_task = asyncio.create_task(self.deliver())
            if getattr(self, 'progress_task', None) is None or self.progress_task.done():
                self.progress_task = asyncio.create_task(self.progress())
            # Gateway resume covers transient gaps; history also covers cold restarts.
            channels = [config['channel_id']]
            with fe_board.Board() as b:
                channels += [quick.channel(b)] if quick.channel(b) else []
                channels += [r[0] for r in b.q('SELECT channel_id FROM pm_discord_threads')]
                channels += [r[0] for r in b.q('SELECT channel_id FROM pm_goal_threads')]
            for cid in channels:
                try:
                    channel = self.get_channel(int(cid)) or await self.fetch_channel(int(cid))
                    with fe_board.Board() as b:
                        cursor = store.setting(b, 'discord_cursor:' + cid)
                    after = discord.Object(id=int(cursor)) if cursor else datetime.datetime.fromtimestamp(
                        config.get('created', time.time()), datetime.timezone.utc)
                    async for message in channel.history(limit=None, after=after, oldest_first=True):
                        await self.on_message(message)
                except discord.HTTPException:
                    LOG.exception('Could not reconcile Discord channel %s', cid)

        async def on_error(self, event_method, *args, **kwargs):
            LOG.exception('Discord event failed: %s', event_method)
            if event_method == 'on_ready':
                self.fatal_error = 'Discord startup failed; inspect manager.log for channel permissions/configuration'
                await self.close()

        def in_talk(self, user, guild, channel):
            """The owner, in the talk channel itself (D317): answered, never acted on."""
            with fe_board.Board() as b:
                cid = quick.channel(b)
            return bool(cid) and str(channel.id) == cid and str(user.id) in config.get('owner_ids', [config['owner_id']]) \
                and str(getattr(guild, 'id', None)) == config['guild_id']

        async def on_message(self, message):
            talking = not message.author.bot and self.in_talk(message.author, message.guild, message.channel)
            if message.author.bot or not (talking or self.allowed(message.author, message.guild, message.channel)):
                return
            body = message.content
            mobile = studio_config.adapter('mobile')
            if mobile is not None and str(message.channel.id) == config['channel_id']:
                with fe_board.Board() as b:
                    if mobile.build_message(b, body, 'discord:' + str(message.id)):
                        wake.set()
                        return
            marks, skipped = [], []
            for a in message.attachments[:10]:
                why = refusal(a.filename, a.content_type, a.size)
                if not why:
                    try:
                        marks.append(attachment_mark(await a.read(), a.filename, a.content_type))
                    except fe_board.BoardError as e:
                        why = str(e)
                    except discord.HTTPException:
                        LOG.warning('Could not import attachment %s', a.id)
                        why = 'Discord would not hand it over'
                if why:
                    skipped.append(f'{a.filename} ({why})')
            body += ('\n' + '\n'.join(marks)) if marks else ''
            if not body.strip() and not skipped:
                return
            event_id = 'discord:' + str(message.id)
            key = 'discord_cursor:' + str(message.channel.id)
            if talking:
                with fe_board.Board() as b, b.tx():
                    if skipped:
                        store.notify(b, 'quick-skipped:' + event_id, skipped_note(skipped, bool(body.strip())))
                    if body.strip():
                        store.receive(b, event_id, 'ask', body, author=message.author.id)
                    store.set_setting(b, key, max(int(store.setting(b, key, '0')), message.id))
                wake.set()
                return
            with fe_board.Board() as b:
                tid = self.task_for(message.channel)
                gid = None if tid else self.goal_for(message.channel)
                with b.tx():
                    if skipped:
                        # a file left behind is said, never dropped in silence (G27's FBX was)
                        store.notify(b, 'skipped:' + event_id, skipped_note(skipped, bool(body.strip())),
                                     tid, goal=gid)
                    fresh = bool(body.strip()) and store.receive(b, event_id, 'reply', body, tid, goal=gid, author=message.author.id)
                    if fresh and gid:
                        # D226: a planning thread is a conversation; the live line shows its turn
                        if talk.planner_running(b, gid):
                            store.notify(b, 'receipt:' + event_id, 'Got it: the planner reads this as soon as its '
                                         'current turn ends.', goal=gid)
                    elif fresh:
                        # Said from where the manager is: a message queued behind a busy
                        # slot says so and names what holds it (D210). A new message here opens
                        # its planning thread (D226), which is its receipt, unless nothing steps.
                        o = outlook.outlook(b, config, steps=False, docs_too=False)
                        if tid or o['halt']:
                            store.notify(b, 'receipt:' + event_id, outlook.receipt(o, tid), tid)
                    store.set_setting(b, key, max(int(store.setting(b, key, '0')), message.id))
            wake.set()

        async def on_interaction(self, interaction):
            # Slash commands are dispatched by CommandTree. Persistent buttons
            # and modals are routed by their explicit IDs, including after restart.
            if interaction.type not in (discord.InteractionType.component, discord.InteractionType.modal_submit):
                return
            if not self.allowed(interaction.user, interaction.guild, interaction.channel):
                await interaction.response.send_message('This control is restricted to the project owner.', ephemeral=True)
                return
            custom = interaction.data.get('custom_id', '')
            if not custom.startswith('pm:'):
                return
            parts = custom.split(':')
            action = parts[1]
            if action in ('approve', 'why', 'drop', 'reason', 'ans', 'own', 'ownsub',
                          'plan', 'accept', 'changes', 'submit', 'pause'):
                import manager_participants
                with fe_board.Board() as b, b.tx():
                    manager_participants.remember(b, 'interaction:' + str(interaction.id),
                                                  interaction.user.id, self.task_for(interaction.channel),
                                                  self.goal_for(interaction.channel))
            tid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
            if action in ('approve', 'why', 'drop', 'reason'):
                await self.decide(interaction, action, tid)
                return
            if action in ('ans', 'own', 'ownsub'):
                await self.answer(interaction, action, tid, parts)
                return
            if action == 'plan':
                # D226: Approve plan creates the plan's tasks, ready, and they start
                gid = int(parts[2]) if len(parts) > 3 and parts[2].isdigit() else None
                with fe_board.Board() as b:
                    g = b.q1('SELECT plan FROM pm_goals WHERE id=?', gid) if gid else None
                    current = json.loads(g[0])['key'] if g and g[0] else None
                    if current != parts[3]:
                        await interaction.response.send_message(
                            'That plan was replaced or already approved; approve the latest one.', ephemeral=True)
                        return
                    store.receive(b, 'interaction:' + str(interaction.id), 'plan', parts[3], goal=gid, author=interaction.user.id)
                wake.set()
                await interaction.response.send_message('Approved: creating the tasks.', ephemeral=True)
                return
            if action in ('accept', 'changes', 'submit'):
                refusal = self.review_refusal(tid, parts)
                if refusal:
                    await interaction.response.send_message(refusal, ephemeral=True)
                    return
            if action == 'changes':
                class Changes(discord.ui.Modal, title='Request changes'):
                    feedback = discord.ui.TextInput(label='What should change?', style=discord.TextStyle.paragraph)
                await interaction.response.send_modal(Changes(custom_id=f'pm:submit:{tid}:{parts[3]}'))
                return
            body = parts[3] if action == 'accept' else ''
            if action == 'submit':
                body = '\n'.join(c.get('value', '') for row in interaction.data.get('components', [])
                                 for c in row.get('components', []))
                action = 'reply'
            if action not in ('accept', 'reply', 'pause'):
                return
            with fe_board.Board() as b:
                store.receive(b, 'interaction:' + str(interaction.id), action, body, tid, author=interaction.user.id)
            wake.set()
            await interaction.response.send_message('Recorded. The manager will follow up here.', ephemeral=True)

        def review_refusal(self, tid, parts):
            """Why an Accept or Request changes click is not taken, or None. The task's phase
            does not matter, only whether it is open and the button names its latest commit (D284)."""
            with fe_board.Board() as b:
                t = b.q1('SELECT coalesce(m.landed_head, m.head) AS head, t.status FROM pm_tasks m '
                         'JOIN tasks t ON t.id=m.task_id WHERE m.task_id=?', tid)
            if not t or len(parts) != 4:
                return 'This review is outdated; use the latest task result.'
            if t['status'] in ('done', 'dropped'):
                return f'T{tid} is already {"accepted" if t["status"] == "done" else "dropped"}.'
            if not t['head']:
                return f'T{tid} has not landed yet.'
            if not t['head'].startswith(parts[3]):
                return (f'A newer result of T{tid} has landed (commit {t["head"][:12]}); this button is for '
                        f'{parts[3]}. Use the buttons on the latest message in its thread.')
            return None

        async def answer(self, interaction, action, tid, parts):
            """D224: a worker's questions. A button records one option; the form answers them
            all in his own words. The last answer resumes the worker with every answer."""
            aid = parts[3] if len(parts) > 3 else ''
            with fe_board.Board() as b:
                r = brief.open_asks(b, aid)
            if not r:
                await interaction.response.send_message(
                    'These questions are already answered or outdated; reply in the thread instead.', ephemeral=True)
                return
            asks = json.loads(r['asks'])
            got = {int(k): v for k, v in json.loads(r['answers']).items()}
            if action == 'own':
                form = discord.ui.Modal(title=(f'Answer T{tid}' if tid else 'Answer the planner')[:45],
                                        custom_id=f'pm:ownsub:{parts[2]}:{aid}')
                for i, a in enumerate(asks[:5]):
                    q, opts = brief.ask_parts(a)
                    form.add_item(discord.ui.TextInput(
                        label=f'{i + 1}. {q}'[:45], style=discord.TextStyle.paragraph, required=False,
                        placeholder=(' / '.join(opts) or q)[:100], default=got.get(i) or None, max_length=1500))
                await interaction.response.send_modal(form)
                return
            if action == 'ans':
                i, j = int(parts[4]), int(parts[5])
                answers = {i: brief.ask_parts(asks[i])[1][j]}
            else:
                values = [c.get('value', '') for row in interaction.data.get('components', [])
                          for c in row.get('components', [])]
                answers = dict(enumerate(values))
            with fe_board.Board() as b:
                try:
                    reply, n, total = brief.answer(b, aid, answers, final=action == 'ownsub')
                except ValueError as e:
                    await interaction.response.send_message(str(e), ephemeral=True)
                    return
                if reply:
                    store.receive(b, 'interaction:' + str(interaction.id), 'reply', reply, tid,
                                  goal=r.get('goal_id') or None, author=interaction.user.id)
            if reply:
                wake.set()
                text = f'Recorded. {f"T{tid}" if tid else "The planner"} goes on with your answers.'
            else:
                text = f'Recorded ({n} of {total} answered). Answer the rest and it goes on.'
            if action == 'ans':
                text = f'{brief.ask_parts(asks[i])[0]} → **{answers[i]}**. ' + text
            await interaction.response.send_message(text, ephemeral=True)

        async def decide(self, interaction, action, tid):
            """A proposal's buttons (D195): Approve, Edit reasoning (a form holding the
            current Why), Drop; the manager checks it is still a proposal when it acts."""
            with fe_board.Board() as b:
                t = b.q1('SELECT t.status, t.body FROM tasks t JOIN pm_tasks m ON m.task_id=t.id '
                         'WHERE t.id=?', tid)
            if not t or t['status'] != 'idea':
                await interaction.response.send_message(
                    f'T{tid} is no longer a proposal' + (f': it is {t["status"]} on the board.' if t else '.'),
                    ephemeral=True)
                return
            if action == 'why':
                form = discord.ui.Modal(title=f'Why T{tid} is done'[:45], custom_id=f'pm:reason:{tid}')
                form.add_item(discord.ui.TextInput(
                    label='Reasoning (its worker tests against it)'[:45], style=discord.TextStyle.paragraph,
                    default=store.reasoning(t['body'])[:4000] or None, max_length=4000))
                await interaction.response.send_modal(form)
                return
            body = '\n'.join(c.get('value', '') for row in interaction.data.get('components', [])
                             for c in row.get('components', [])) if action == 'reason' else ''
            with fe_board.Board() as b:
                store.receive(b, 'interaction:' + str(interaction.id), 'approve' if action == 'approve'
                              else 'drop' if action == 'drop' else 'reason', body, tid, author=interaction.user.id)
            wake.set()
            await interaction.response.send_message(
                {'approve': 'Approved.', 'drop': 'Dropped.'}.get(action, 'Reasoning saved.')
                + ' The manager will follow up here.', ephemeral=True)

        async def destination(self, b, row):
            if quick.is_quick(row) and quick.channel(b):
                cid = int(quick.channel(b))  # D317: an answer goes where he asked
                return self.get_channel(cid) or await self.fetch_channel(cid)
            root =self.get_channel(int(config['channel_id'])) or await self.fetch_channel(int(config['channel_id']))
            if not row['task_id'] and row.get('goal_id'):
                return await self.goal_thread(b, root, row['goal_id'])
            if not row['task_id']:
                return root
            tid = row['task_id']
            mapped = b.q1('SELECT channel_id FROM pm_discord_threads WHERE task_id=?', tid)
            if mapped:
                channel = self.get_channel(int(mapped[0])) or await self.fetch_channel(int(mapped[0]))
                if channel.archived:
                    await channel.edit(archived=False)
                return channel
            # Stable task prefix recovers a thread created just before a crash.
            prefix = f'T{tid} | '
            found = next((t for t in root.threads if t.name.startswith(prefix)), None)
            if not found:
                async for t in root.archived_threads(limit=None):
                    if t.name.startswith(prefix):
                        found = t
                        await found.edit(archived=False)
                        break
            # The goal's name first (D193), so the thread list says what each task is part of.
            goal = b.q1('SELECT g.name FROM pm_tasks m JOIN pm_goals g ON g.id=m.goal_id WHERE m.task_id=?', tid)
            name = prefix + (f'{goal[0]}: ' if goal and goal[0] else '') + b.task(tid)['title']
            channel = found or await root.create_thread(name=name[:100],
                                                        type=discord.ChannelType.public_thread,
                                                        auto_archive_duration=1440)
            b.con.execute('INSERT OR REPLACE INTO pm_discord_threads VALUES(?,?)', (tid, str(channel.id)))
            return channel

        async def goal_thread(self, b, root, gid):
            """D226: a goal's planning thread, opened on Yotam's own message when it came from here."""
            mapped = talk.thread_of(b, gid)
            if mapped:
                channel = self.get_channel(int(mapped)) or await self.fetch_channel(int(mapped))
                if channel.archived:
                    await channel.edit(archived=False)
                return channel
            g = b.q1('SELECT name, body, source FROM pm_goals WHERE id=?', gid)
            name = f'G{gid} | ' + (g['name'] or ' '.join(g['body'].split())[:80])
            channel = None
            if g['source'].startswith('discord:') and g['source'][8:].isdigit():
                try:
                    channel = await root.get_partial_message(int(g['source'][8:])).create_thread(
                        name=name[:100], auto_archive_duration=10080)
                except discord.HTTPException:
                    channel = None  # the message is gone, or already has a thread
            if channel is None:
                channel = await root.create_thread(name=name[:100], type=discord.ChannelType.public_thread,
                                                   auto_archive_duration=10080)
            b.con.execute('INSERT OR REPLACE INTO pm_goal_threads VALUES(?,?)', (gid, str(channel.id)))
            return channel

        async def progress(self):
            """While a run works, its thread carries one live message, edited as it goes, as a
            Claude Code session shows its steps; it is removed when the run ends (D226)."""
            import zlib
            while not self.is_closed():
                await asyncio.sleep(6)
                try:
                    with fe_board.Board() as b:
                        live = {}
                        for r in b.q("SELECT * FROM pm_runs WHERE state='running'"):
                            cid = run_thread(b, r)
                            if cid:
                                live[r['id']] = (cid, progress_text(b, dict(r)))
                        shown = {k[len('progress:'):]: v for k, v in
                                 b.q("SELECT key, value FROM pm_settings WHERE key LIKE 'progress:%'")}
                    for rid, (cid, text) in live.items():
                        channel = self.get_channel(int(cid)) or await self.fetch_channel(int(cid))
                        mid, _, last = (shown.get(rid) or '').partition('|')
                        if mid and last == str(zlib.crc32(text.encode())):
                            continue
                        if mid:
                            try:
                                await channel.get_partial_message(int(mid)).edit(content=text)
                            except discord.NotFound:
                                mid = ''
                        if not mid:
                            mid = str((await channel.send(text, allowed_mentions=discord.AllowedMentions.none())).id)
                        with fe_board.Board() as b:
                            store.set_setting(b, 'progress:' + rid, f'{mid}|{zlib.crc32(text.encode())}')
                    for rid in set(shown) - set(live):
                        mid = shown[rid].partition('|')[0]
                        with fe_board.Board() as b:
                            r = b.q1('SELECT * FROM pm_runs WHERE id=?', rid)
                            cid = run_thread(b, r) if r else None
                            b.con.execute('DELETE FROM pm_settings WHERE key=?', ('progress:' + rid,))
                        if cid:
                            try:
                                channel = self.get_channel(int(cid)) or await self.fetch_channel(int(cid))
                                await channel.get_partial_message(int(mid)).delete()
                            except discord.HTTPException:
                                pass
                except (discord.HTTPException, OSError, ValueError):
                    LOG.exception('Discord progress update failed')

        async def send_message(self, channel, row, embed, view, paths, first):
            """One message. Discord refusing the attachments (400) must not keep the message,
            and the controls on it, from arriving: it is sent again naming what was left out."""
            import manager_participants
            with fe_board.Board() as b:
                people = manager_participants.recipients(b, row, config) if row['ping'] and first else []
            def send(files):
                return channel.send(content=' '.join(f'<@{person}>' for person in people) or None,
                                    embed=embed, view=view, files=files,
                                    allowed_mentions=discord.AllowedMentions(
                                        users=[discord.Object(id=int(person)) for person in people], roles=False, everyone=False))
            files = [discord.File(str(p)) for p in paths]
            try:
                return await send(files)
            except discord.HTTPException as e:
                if e.status != 400 or not paths:
                    raise
                LOG.warning('Discord refused the attachments of event %s (%s); sending without them', row['id'], e.text)
                embed.add_field(name='Attachments Discord refused (on the board under the task)',
                                value=', '.join(p.name for p in paths)[:1000])
                return await send([])
            finally:
                for f in files:
                    f.close()

        async def deliver_row(self, row):
            with fe_board.Board() as b:
                channel = await self.destination(b, row)
                task = b.q1('SELECT m.*, t.status FROM pm_tasks m JOIN tasks t ON t.id=m.task_id '
                            'WHERE m.task_id=?', row['task_id']) if row['task_id'] else None
            marker = f'pm-event:{row["id"]}:'
            delivered = {}
            if row['attempts']:
                # Reconcile the send/ack crash window against Discord itself.
                after = datetime.datetime.fromtimestamp(config.get('created', 0), datetime.timezone.utc)
                async for msg in channel.history(limit=None, after=after):
                    if msg.author.id == self.user.id:
                        for embed in msg.embeds:
                            footer = embed.footer.text or ''
                            if footer.startswith(marker):
                                delivered[footer] = str(msg.id)
            chunks = [row['body'][i:i+3500] for i in range(0, len(row['body']), 3500)] or ['Update']
            ids = []
            kind, _, rest = row['dedup'].partition(':')
            review_head = rest.partition(':')[2] if row['task_id'] and kind in ('landed', 'accept-control') else None
            paths, oversize = [], []
            for name in json.loads(row['files']):
                p = Path(name).resolve()
                if p.is_relative_to(fe_board.files_dir().resolve()) and p.is_file():
                    limit = getattr(getattr(channel, 'guild', None), 'filesize_limit', 10 << 20)
                    if p.stat().st_size <= limit:
                        paths.append(p)
                    else:
                        oversize.append(p.name)
            # Discord takes ten attachments to a message: the rest follow in messages of their own.
            batches = [paths[n:n + MAX_ATTACHMENTS] for n in range(0, len(paths), MAX_ATTACHMENTS)]
            for i, chunk in enumerate(chunks):
                key = marker + str(i)
                if key in delivered:
                    ids.append(delivered[key])
                    continue
                embed = discord.Embed(description=chunk)
                embed.set_footer(text=key)
                view = discord.ui.View(timeout=None)
                if i == 0 and review_head and task and task['status'] not in ('done', 'dropped'):
                    # The message is a review of that commit and carries its buttons whenever it is
                    # sent, whatever the task has done since it was queued (D284).
                    for label, action, style in [('Accept', 'accept', discord.ButtonStyle.success),
                                                  ('Request changes', 'changes', discord.ButtonStyle.secondary)]:
                        view.add_item(discord.ui.Button(label=label, style=style,
                                      custom_id=f'pm:{action}:{row["task_id"]}:{review_head[:12]}'))
                if i == 0 and task and task['status'] == 'idea' and row['dedup'].startswith(('task:', 'proposal:')):
                    # A proposal (D195): nothing starts before Approve.
                    for label, action, style in [('Approve', 'approve', discord.ButtonStyle.success),
                                                  ('Edit reasoning', 'why', discord.ButtonStyle.primary),
                                                  ('Drop', 'drop', discord.ButtonStyle.danger)]:
                        view.add_item(discord.ui.Button(label=label, style=style,
                                                        custom_id=f'pm:{action}:{row["task_id"]}'))
                if i == 0 and row['dedup'].startswith('plan:') and row.get('goal_id'):
                    with fe_board.Board() as b:
                        g = b.q1('SELECT plan FROM pm_goals WHERE id=?', row['goal_id'])
                    key = row['dedup'].rsplit(':', 1)[1]
                    if g and g[0] and json.loads(g[0])['key'] == key:
                        view.add_item(discord.ui.Button(label='Approve plan', style=discord.ButtonStyle.success,
                                                        custom_id=f'pm:plan:{row["goal_id"]}:{key}'))
                with fe_board.Board() as b:
                    ask = b.q1('SELECT * FROM pm_asks WHERE dedup=? AND done=0', row['dedup']) if i == 0 else None
                who = row['task_id'] or f'g{row.get("goal_id")}'  # a planner's question has no task
                if ask:
                    # D224: a button per option, a row per question (the recommended option first,
                    # in green), and a form for answering in his own words.
                    for n, a in enumerate(json.loads(ask['asks'])[:4]):
                        q, opts = brief.ask_parts(a)
                        for j, o in enumerate(opts if len(opts) > 1 else []):
                            view.add_item(discord.ui.Button(
                                label=f'{n + 1}{chr(97 + j)}. {o}'[:80], row=n,
                                style=discord.ButtonStyle.success if j == 0 else discord.ButtonStyle.secondary,
                                custom_id=f'pm:ans:{who}:{ask["id"]}:{n}:{j}'))
                    view.add_item(discord.ui.Button(label='Answer in my own words', row=4,
                                                    style=discord.ButtonStyle.primary,
                                                    custom_id=f'pm:own:{who}:{ask["id"]}'))
                if i == 0 and not quick.is_answer(row):
                    view.add_item(discord.ui.Button(label='Pause', custom_id='pm:pause', row=4 if ask else None))
                for name in oversize if i == 0 else ():
                    embed.add_field(name='Evidence image too large for Discord', value=name[:200])
                msg = await self.send_message(channel, row, embed, view, batches[0] if i == 0 and batches else [], i == 0)
                ids.append(str(msg.id))
            for n, batch in enumerate(batches[1:], 1):
                fkey = f'{marker}f{n}'
                if fkey in delivered:
                    ids.append(delivered[fkey])
                    continue
                more = discord.Embed(description=f'Attachments, continued ({n + 1} of {len(batches)})')
                more.set_footer(text=fkey)
                msg = await self.send_message(channel, row, more, discord.ui.View(timeout=None), batch, False)
                ids.append(str(msg.id))
            if row['dedup'].startswith('close:') and isinstance(channel, discord.Thread):
                # The task is closed on the board (D192); the next message to it reopens it.
                await channel.edit(archived=True)
            with fe_board.Board() as b:
                b.con.execute('UPDATE pm_outbox SET sent=? WHERE id=?', (json.dumps(ids), row['id']))
                if review_head and task and task['status'] not in ('done', 'dropped'):
                    # Remembered, so the repair pass in reconcile stays quiet for this commit (D284).
                    store.set_setting(b, store.control_key(row['task_id'], review_head), '1')

        async def deliver(self):
            while not self.is_closed():
                await self.wait_until_ready()
                with fe_board.Board() as b:
                    rows = [dict(r) for r in b.q('SELECT * FROM pm_outbox WHERE sent IS NULL AND retry_at<=? ORDER BY id LIMIT 10', time.time())]
                for row in rows:
                    # Persist intent before network I/O, including the first send.
                    with fe_board.Board() as b:
                        b.con.execute('UPDATE pm_outbox SET attempts=attempts+1 WHERE id=?', (row['id'],))
                    try:
                        await self.deliver_row(row)
                    except Exception:  # one bad row must never end delivery for the rest (D284)
                        LOG.exception('Discord delivery failed for event %s', row['id'])
                        with fe_board.Board() as b:
                            b.con.execute('UPDATE pm_outbox SET retry_at=? WHERE id=?',
                                          (time.time() + min(300, 2 ** min(row['attempts'] + 1, 8)), row['id']))
                await asyncio.sleep(2)

    client = Client()
    guild = discord.Object(id=int(config['guild_id']))

    async def perform(interaction, action):
        if not client.allowed(interaction.user, interaction.guild, interaction.channel):
            await interaction.response.send_message('Restricted to the project owner and project channel.', ephemeral=True)
            return
        with fe_board.Board() as b:
            if action == 'status':
                text = '\n'.join(outlook.lines(outlook.outlook(b, config), md=True))
                if len(text) > 1900:  # Discord's limit: drop the last lines whole, never mid-line
                    text = text[:1880].rsplit('\n', 1)[0] + '\n…'
            elif action == 'models':
                import manager_models  # D218: each model's record, the routing and what each task spent
                text = '\n'.join(manager_models.report(b, last=8))[:1900]
            else:
                store.receive(b, 'interaction:' + str(interaction.id), action, author=interaction.user.id)
                text = f'{action.capitalize()} recorded.'
        wake.set()
        await interaction.response.send_message(text, ephemeral=True)

    # Separate callbacks keep app_commands' parameter introspection simple.
    @client.tree.command(name='status', description='Show project manager status', guild=guild)
    async def status(interaction: discord.Interaction):
        await perform(interaction, 'status')

    @client.tree.command(name='models', description='Which model runs each task, and what each spent', guild=guild)
    async def models_(interaction: discord.Interaction):
        await perform(interaction, 'models')

    @client.tree.command(name='pause', description='Finish active runs but start no new work', guild=guild)
    async def pause(interaction: discord.Interaction):
        await perform(interaction, 'pause')

    @client.tree.command(name='resume', description='Resume work on approved goals', guild=guild)
    async def resume(interaction: discord.Interaction):
        await perform(interaction, 'resume')

    @client.tree.command(name='stop', description='Stop managed workers and preserve their work', guild=guild)
    async def stop(interaction: discord.Interaction):
        await perform(interaction, 'stop')

    @client.tree.command(name='ask', description='Ask about this task; answered here, nothing changes', guild=guild)
    @app_commands.describe(question='What you want to know')
    async def ask(interaction: discord.Interaction, question: str):
        """D322: a question in a task's thread that reaches no worker (a plain reply does, D212)."""
        talking = client.in_talk(interaction.user, interaction.guild, interaction.channel)
        if not (talking or client.allowed(interaction.user, interaction.guild, interaction.channel)):
            await interaction.response.send_message('Restricted to the project owner and project channel.', ephemeral=True)
            return
        tid = None if talking else client.task_for(interaction.channel)
        if not tid and not talking:
            await interaction.response.send_message(f"Use /ask in a task's thread, or just ask in #{quick.CHANNEL}.",
                                                    ephemeral=True)
            return
        with fe_board.Board() as b:
            store.receive(b, 'interaction:' + str(interaction.id), 'ask', question, tid, author=interaction.user.id)
        wake.set()
        await interaction.response.send_message(
            f'**/ask** {question[:1700]}\n-# Answering here; nothing changes {f"T{tid}" if tid else "the work"}.',
            allowed_mentions=discord.AllowedMentions.none())
    return client
