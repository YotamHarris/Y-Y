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


if __name__ == '__main__':
    unittest.main()
