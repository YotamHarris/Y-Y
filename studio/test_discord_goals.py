"""Offline Discord goal tests, independent of a game's adapters or credentials."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord
import fe_board as board
import manager_brief as brief
import manager_core as core
import manager_discord as transport
import manager_participants as participants
import manager_store as store
import manager_talk as talk


class DiscordGoalsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'FE_BOARD_DIR': self.tmp.name})
        self.env.start()
        self.registry = patch.object(board, 'registry', return_value={'remote': 'origin', 'branch': 'main', 'agents': {}})
        self.registry.start()
        self.b = board.Board()
        self.config = dict(owner_id='11', owner_ids=['11', '22'], guild_id='33', channel_id='44',
                           repo=self.tmp.name, created=0, nightly_crew='off')

    def tearDown(self):
        self.b.close()
        self.registry.stop()
        self.env.stop()
        self.tmp.cleanup()

    def goal(self, source='discord:1', author='22'):
        store.receive(self.b, source, 'reply', 'Improve the game', author=author)
        with self.b.tx():
            return talk.start(self.b, source, 'Improve the game')

    def test_goal_participants_receive_planner_task_and_completion_pings(self):
        gid = self.goal()
        row = dict(goal_id=gid, task_id=None)
        self.assertEqual(participants.recipients(self.b, row, self.config), ['22'])
        # A reply from the other allowed developer joins this goal.
        store.receive(self.b, 'discord:2', 'reply', 'Yes', goal=gid, author='11')
        tids = store.plan_tasks(self.b, gid, [dict(title='Change game', body='Update game',
                               reasoning='Why: improve play', depends=[])], approved=True, key='test')
        tid = tids[0]
        talk.answered(self.b, dict(goal_id=gid, id='planner1'), dict(status='blocked', summary='Planner reply', asks=[]))
        brief.post(self.b, 'question', tid, 'Task question', ['Continue?'], ping=True)
        self.b.con.execute("UPDATE tasks SET status='done' WHERE id=?", (tid,))
        core.Manager(self.b, self.config).reconcile()
        rows = [dict(r) for r in self.b.q('SELECT * FROM pm_outbox WHERE ping=1')]
        self.assertEqual(len(rows), 3)
        self.assertTrue(any(r['dedup'] == f'goal-complete:{gid}' for r in rows))
        async def exercise():
            client = transport.build_client(self.config, asyncio.Event())
            channel = Mock(send=AsyncMock(return_value=Mock(id=1)))
            client.destination = AsyncMock(return_value=channel)
            for r in rows:
                await client.deliver_row(r)
            for call in channel.send.await_args_list:
                self.assertEqual(call.kwargs['content'], '<@11> <@22>')
                self.assertEqual([u.id for u in call.kwargs['allowed_mentions'].users], [11, 22])
            await client.close()
        asyncio.run(exercise())
        self.config['owner_ids'] = ['22']
        self.assertEqual(participants.recipients(self.b, dict(task_id=tid), self.config), ['22'])
        self.config['owner_ids'] = []
        self.assertEqual(participants.recipients(self.b, row, self.config), [])

    def test_legacy_goals_global_alerts_and_duplicates_keep_their_identity(self):
        legacy = self.goal('local:1', author=None)
        store.receive(self.b, 'discord:2', 'reply', 'Joined later', goal=legacy, author='22')
        self.assertEqual(participants.recipients(self.b, dict(goal_id=legacy), self.config), ['11'])
        self.assertEqual(participants.recipients(self.b, {}, self.config), ['11'])
        gid = self.goal('discord:3')
        self.assertFalse(store.receive(self.b, 'discord:3', 'reply', 'Replay', goal=gid, author='11'))
        self.assertEqual(participants.recipients(self.b, dict(goal_id=gid), self.config), ['22'])
        participants.ensure_tables(self.b.con)
        participants.ensure_tables(self.b.con)
        self.assertEqual(self.b.q1('SELECT requester_id FROM pm_goals WHERE id=?', gid)[0], '22')

    def test_transport_records_authors_and_partial_answer_buttons(self):
        async def exercise():
            client = transport.build_client(self.config, asyncio.Event())
            msg = Mock(id=1, content='New goal', author=Mock(id=22, bot=False),
                       guild=Mock(id=33), channel=Mock(id=44, parent_id=None), attachments=[])
            with patch.object(transport.outlook, 'outlook', return_value={'halt': ''}):
                await client.on_message(msg)
            self.assertEqual(self.b.q1('SELECT author_id FROM pm_authors WHERE event_id=?', 'discord:1')[0], '22')
            core.Manager(self.b, self.config).input_events()
            gid = self.b.q1('SELECT id FROM pm_goals')[0]
            self.b.con.execute('INSERT INTO pm_goal_threads VALUES(?,?)', (gid, '55'))
            talk.answered(self.b, dict(goal_id=gid, id='planner2'), dict(status='blocked', summary='Questions', asks=[
                dict(question='First?', options=['Yes', 'No']), dict(question='Second?', options=['Yes', 'No'])]))
            aid = self.b.q1('SELECT id FROM pm_asks')[0]
            click = Mock(id=2, type=discord.InteractionType.component, user=Mock(id=11), guild=Mock(id=33),
                         channel=Mock(id=55, parent_id=44), data={'custom_id': f'pm:ans:g{gid}:{aid}:0:0'},
                         response=Mock(send_message=AsyncMock()))
            await client.on_interaction(click)
            self.assertEqual(participants.recipients(self.b, dict(goal_id=gid), self.config), ['11', '22'])
            self.assertFalse(self.b.q1('SELECT 1 FROM pm_inbox WHERE id=?', 'interaction:2'))
            # Unauthorized messages cannot become a participant or an inbox entry.
            msg.id, msg.author.id, msg.channel.id, msg.channel.parent_id = 3, 99, 55, 44
            await client.on_message(msg)
            self.assertIsNone(self.b.q1('SELECT 1 FROM pm_authors WHERE author_id=?', '99'))
            await client.close()
        asyncio.run(exercise())

    def test_allowlist_keeps_guild_channel_and_thread_checks(self):
        for user in ('11', '22'):
            self.assertTrue(transport.authorized(self.config, user, '33', '44'))
            self.assertTrue(transport.authorized(self.config, user, '33', '55', '44', ['55']))
            self.assertFalse(transport.authorized(self.config, user, '99', '44'))
            self.assertFalse(transport.authorized(self.config, user, '33', '55', '44', []))
        self.assertFalse(transport.authorized(self.config, '99', '33', '44'))
        del self.config['owner_ids']
        self.assertTrue(transport.authorized(self.config, '11', '33', '44'))
        self.assertFalse(transport.authorized(self.config, '22', '33', '44'))

    def test_long_planner_reply_preserves_four_questions_and_puts_buttons_last(self):
        gid = self.goal()
        asks = [dict(question=f'Question {n}: ' + 'details ' * 60, options=['Continue', 'Change'])
                for n in range(1, 5)]
        result = dict(status='blocked', summary=('A paragraph of context. ' * 35 + '\n\n') * 4, asks=asks)
        talk.answered(self.b, dict(goal_id=gid, id='longreply'), result)
        row = dict(self.b.q1("SELECT * FROM pm_outbox WHERE dedup LIKE 'talk:%'"))
        body = row['body']
        parts = transport.message_parts(body)
        self.assertEqual(''.join(parts), body)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 4096 for p in parts))
        for n, ask in enumerate(asks, 1):
            question = f'**{n}.** ' + brief.ask_line(ask)
            self.assertTrue(any(question in p for p in parts), question)
        async def exercise():
            client = transport.build_client(self.config, asyncio.Event())
            channel = Mock(send=AsyncMock(side_effect=[Mock(id=i) for i in range(len(parts))]))
            client.destination = AsyncMock(return_value=channel)
            await client.deliver_row(row)
            calls = channel.send.await_args_list
            self.assertEqual(''.join(c.kwargs['embed'].description for c in calls), body)
            self.assertEqual([bool(c.kwargs['view'].children) for c in calls], [False] * (len(parts) - 1) + [True])
            labels = [c.label for c in calls[-1].kwargs['view'].children]
            self.assertIn('4a. Continue', labels)
            self.assertIn('Answer in my own words', labels)
            self.assertEqual(calls[0].kwargs['content'], '<@22>')
            self.assertTrue(all(c.kwargs['content'] is None for c in calls[1:]))
            # A crash after all but the last part resumes with just the final part.
            async def history(**kw):
                for i, call in enumerate(calls[:-1]):
                    yield Mock(author=Mock(id=777), embeds=[call.kwargs['embed']], id=i)
            channel.history = history
            channel.send = AsyncMock(return_value=Mock(id=len(parts) - 1))
            client._connection.user = Mock(id=777)
            row['attempts'] = 1
            await client.deliver_row(row)
            self.assertEqual(channel.send.await_count, 1)
            self.assertTrue(channel.send.await_args.kwargs['view'].children)
            self.assertEqual(json.loads(self.b.q1('SELECT sent FROM pm_outbox WHERE id=?', row['id'])[0]),
                             [str(i) for i in range(len(parts))])
            await client.close()
        asyncio.run(exercise())

    def test_message_parts_preserves_boundaries_long_lines_and_unicode(self):
        for body in ('', 'short', 'x' * 4096, 'x' * 4097, 'a\r\n\r\nb\n', '\n' * 9000,
                     '😀שלום ' * 2000, ('line\n' * 1200),
                     '**1.** First\n\n   **a.** Choice\n**2.** Second\n   **a.** Choice'):
            parts = transport.message_parts(body)
            self.assertEqual(''.join(parts), body or 'Update')
            self.assertTrue(all(0 < len(p) <= 4096 for p in parts))

    def test_plan_proposal_and_review_controls_are_on_the_last_part(self):
        gid = self.goal()
        tasks = [dict(title='Change game', body='Update game', reasoning='Why: improve play', depends=[])]
        talk.answered(self.b, dict(goal_id=gid, id='plan123'),
                      dict(status='planned', summary='Context.\n\n' * 1000, tasks=tasks))
        plan = dict(self.b.q1("SELECT * FROM pm_outbox WHERE dedup LIKE 'plan:%'"))
        tid = store.plan_tasks(self.b, gid, tasks, approved=False, key='proposal')[0]
        store.notify(self.b, 'proposal:test', 'Context.\n\n' * 1000, task=tid)
        proposal = dict(self.b.q1("SELECT * FROM pm_outbox WHERE dedup='proposal:test'"))
        store.notify(self.b, 'landed:test:' + 'a' * 40, 'Context.\n\n' * 1000, task=tid)
        review = dict(self.b.q1("SELECT * FROM pm_outbox WHERE dedup LIKE 'landed:%'"))
        async def exercise():
            client = transport.build_client(self.config, asyncio.Event())
            client._connection.user = Mock(id=777)
            channel = Mock(send=AsyncMock(return_value=Mock(id=1)))
            client.destination = AsyncMock(return_value=channel)
            for row, labels in ((plan, ['Approve plan', 'Pause']),
                                (proposal, ['Approve', 'Edit reasoning', 'Drop', 'Pause']),
                                (review, ['Accept', 'Request changes', 'Pause'])):
                channel.send.reset_mock()
                await client.deliver_row(row)
                calls = list(channel.send.await_args_list)
                self.assertGreater(len(calls), 1)
                self.assertTrue(all(not c.kwargs['view'].children for c in calls[:-1]))
                self.assertEqual([c.label for c in calls[-1].kwargs['view'].children], labels)
                async def history(**kw):
                    for call in calls:
                        yield Mock(author=Mock(id=777), embeds=[call.kwargs['embed']], id=1)
                channel.history = history
                channel.send.reset_mock()
                row['attempts'] = 1
                await client.deliver_row(row)
                channel.send.assert_not_awaited()
            await client.close()
        asyncio.run(exercise())

    def test_controls_follow_overflow_attachments_and_retry_keeps_them_last(self):
        gid = self.goal()
        paths = []
        for n in range(11):
            p = board.files_dir() / f'{n}.txt'
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('Evidence', encoding='utf-8')
            paths.append(str(p))
        store.notify(self.b, 'attachment-chain', 'Context.\n\n' * 1000, goal=gid, ping=True, files=paths)
        row = dict(self.b.q1('SELECT * FROM pm_outbox WHERE dedup=?', 'attachment-chain'))
        async def exercise():
            client = transport.build_client(self.config, asyncio.Event())
            client._connection.user = Mock(id=777)
            channel = Mock(send=AsyncMock(return_value=Mock(id=1)), guild=Mock(filesize_limit=100000))
            client.destination = AsyncMock(return_value=channel)
            await client.deliver_row(row)
            calls = channel.send.await_args_list
            self.assertTrue(all(not c.kwargs['view'].children for c in calls[:-1]))
            self.assertEqual([c.label for c in calls[-1].kwargs['view'].children], ['Pause'])
            self.assertEqual(len(calls[0].kwargs['files']), 10)
            self.assertEqual(len(calls[-1].kwargs['files']), 1)
            async def history(**kw):
                for call in calls[:-1]:
                    yield Mock(author=Mock(id=777), embeds=[call.kwargs['embed']], id=1)
            channel.history = history
            channel.send = AsyncMock(return_value=Mock(id=2))
            row['attempts'] = 1
            await client.deliver_row(row)
            self.assertEqual(channel.send.await_count, 1)
            self.assertEqual([c.label for c in channel.send.await_args.kwargs['view'].children], ['Pause'])
            await client.close()
        asyncio.run(exercise())


if __name__ == '__main__':
    unittest.main()
