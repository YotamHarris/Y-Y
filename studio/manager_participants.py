"""Authenticated Discord authors and the people taking part in each goal."""


def ensure_tables(con):
    con.execute("CREATE TABLE IF NOT EXISTS pm_authors(event_id TEXT PRIMARY KEY, author_id TEXT NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS pm_participants(goal_id INTEGER NOT NULL, author_id TEXT NOT NULL, "
                "PRIMARY KEY(goal_id, author_id))")
    cols = {r[1] for r in con.execute('PRAGMA table_info(pm_goals)')}
    if cols and 'requester_id' not in cols:
        con.execute("ALTER TABLE pm_goals ADD COLUMN requester_id TEXT NOT NULL DEFAULT ''")


def goal_for(b, task=None, goal=None):
    if goal:
        return goal
    r = b.q1('SELECT goal_id FROM pm_tasks WHERE task_id=?', task) if task else None
    return r[0] if r else None


def remember(b, event_id, author, task=None, goal=None):
    """Transport has authenticated the author. Replayed events cannot change authors."""
    if not author:
        return
    b.con.execute('INSERT OR IGNORE INTO pm_authors VALUES(?,?)', (str(event_id), str(author)))
    author = b.q1('SELECT author_id FROM pm_authors WHERE event_id=?', str(event_id))[0]
    gid = goal_for(b, task, goal)
    if gid:
        b.con.execute('INSERT OR IGNORE INTO pm_participants VALUES(?,?)', (gid, author))


def opened(b, gid, event_id):
    r = b.q1('SELECT author_id FROM pm_authors WHERE event_id=?', str(event_id))
    if r:
        b.con.execute('UPDATE pm_goals SET requester_id=? WHERE id=?', (r[0], gid))
        b.con.execute('INSERT OR IGNORE INTO pm_participants VALUES(?,?)', (gid, r[0]))


def recipients(b, row, config):
    """Legacy goals and manager-wide alerts retain the configured owner fallback."""
    gid = goal_for(b, row.get('task_id'), row.get('goal_id'))
    g = b.q1('SELECT requester_id FROM pm_goals WHERE id=?', gid) if gid else None
    if not g or not g[0]:
        return [str(config['owner_id'])]
    allowed = set(map(str, config.get('owner_ids', [config['owner_id']])))
    people = {r[0] for r in b.q('SELECT author_id FROM pm_participants WHERE goal_id=?', gid)}
    return sorted((people | {g[0]}) & allowed)
