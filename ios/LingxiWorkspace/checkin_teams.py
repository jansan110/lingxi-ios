"""Plan and join official sign-in teams using accounts in the shared pool."""
from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from math import ceil
from secrets import token_urlsafe
from threading import Lock, RLock
from urllib.parse import parse_qs, urlsplit

from checkin_client import CheckinError, ENTRY
from checkin_scheduler import CheckinTaskError
from feature_runtime import feature_running
from login_storage import LoginStorageError


TEAM_STATES = {
    -1: '未开放组团', 0: '尚未组队', 1: '已组队', 2: '已满员',
    3: '已达标，可领取奖励', 4: '奖励已领取', 5: '奖励已使用', 6: '奖励已过期',
}


def _invite_token(share_url):
    source = urlsplit(share_url or '')
    if (source.scheme, source.netloc, source.path) == (
            'https', 'h.app.coc.10086.cn', '/activity/transit/transferDownload.html'):
        source = urlsplit(parse_qs(source.query).get('targetUrl', [''])[0])
    entry = urlsplit(ENTRY)
    if (source.scheme, source.netloc, source.path) != (entry.scheme, entry.netloc, entry.path):
        return ''
    return parse_qs(source.query).get('groupMarkCode', [''])[0]


def _parse_team(value, phone_masked=''):
    if not isinstance(value, dict):
        raise CheckinError('未取得官方组团状态', 'TEAM_STATUS_MISSING')
    try:
        status = int(value['status'])
        if status == -1:
            return {'status': status, 'message': TEAM_STATES[status]}, ''
        config = value['baseConfig']
        minimum, maximum, goal = (int(config[key]) for key in ('minMemberNum', 'maxMemberNum', 'totalMarkTimes'))
        if status not in TEAM_STATES or not (2 <= minimum <= maximum <= 5 and goal > 0):
            raise ValueError
        group = value.get('myGroup') or {}
        leader = group.get('leader') or ''
        entries = [leader, *(group.get('members') or '').split('|')]
        signed = [int(entry.rsplit('-', 1)[1]) for entry in entries if entry]
        summary = {'status': status, 'message': TEAM_STATES[status], 'members': len(signed),
                   'signed': sum(signed), 'minimum': minimum, 'maximum': maximum, 'goal': goal,
                   'prize': str(config.get('expectPrizeName') or ''),
                   'award': str((value.get('myPrize') or {}).get('name') or '')}
        can_invite = (status == 0 or
                      status == 1 and phone_masked and leader.rsplit('-', 1)[0] == phone_masked[-4:]
                      and summary['members'] < maximum and summary['signed'] < goal)
        return summary, _invite_token(value.get('shareUrl')) if can_invite else ''
    except (KeyError, ValueError, TypeError, IndexError) as error:
        raise CheckinError('官方组团规则或成员状态不完整，本次不组队', 'TEAM_STATUS_INVALID') from error


def _group_rows(rows, minimum, maximum, goal):
    candidates = sorted(rows, key=lambda row: (row['month_count'] or 0, row['id']))
    groups = []
    while len(candidates) >= minimum:
        limit, signed = maximum, 0
        for size, row in enumerate(candidates[:maximum - 1], 1):
            signed += row['month_count'] or 0
            if signed >= goal:
                limit = max(minimum, size)
                break
        size = max(minimum, ceil(len(candidates) / ceil(len(candidates) / limit)))
        # Use the smallest contributions first. A team reaching its goal
        # before the last invitation can no longer take more members.
        groups.append([*candidates[:size - 1], candidates[-1]])
        candidates = candidates[size - 1:-1]
    return groups, candidates


class CheckinTeams:
    def __init__(self, scheduler):
        self.scheduler = scheduler
        self.lock = Lock()
        self.plan = None
        self.award_checks = set()

    @contextmanager
    def _client(self, login_id, *, joining=False):
        with self.scheduler.lock:
            account_lock = self.scheduler.account_locks.setdefault(login_id, RLock())
        with account_lock:
            if joining:
                with self.scheduler.lock:
                    self.scheduler.team_joining.add(login_id)
            try:
                yield self.scheduler.client_factory(lambda: self.scheduler.credentials(login_id))
            finally:
                if joining:
                    with self.scheduler.lock:
                        self.scheduler.team_joining.discard(login_id)

    def _query(self, login_id, phone_masked=''):
        with self._client(login_id) as client:
            return _parse_team(client.team_status(), phone_masked)

    def award_status(self):
        with self.scheduler.lock:
            month = self.scheduler.now().strftime('%Y%m')
            records = [record for record in self.scheduler.data.get('team_awards', {}).values()
                       if record['month'] == month]
            return {'checking': len(self.award_checks),
                    'claimed': sum(record['status'] in (4, 5) for record in records),
                    'unconfirmed': sum(record['submitted'] for record in records)}

    def queue_awards(self, active=None, *, immediate=False, restored=()):
        if active is None:
            active = {row['id'] for row in self.scheduler.accounts() if row['status'] == 'active'}
        now = self.scheduler.now()
        month, day = now.strftime('%Y%m'), now.strftime('%Y%m%d')
        with self.scheduler.lock:
            data = self.scheduler.data
            if self.scheduler.load_error or self.scheduler.quit.is_set() or not feature_running():
                return 0
            waiting_for_signin = False
            if not immediate:
                if now.strftime('%H:%M') < data['automation']['signin_time']:
                    return 0
                waiting_for_signin = any(
                    task['kind'] == 'signin' and task['state'] in ('preparing', 'running', 'stopping')
                    for task in data['tasks'].values())
            ledger = data.get('team_awards', {})
            signins = (data['daily'].get(f'{login_id}:signin:{day}', {}) for login_id in active)
            latest_signin = max((record['time'] for record in signins if record.get('state') == 'signed'
                                 and record.get('submitted')), default='')
            count = 0
            for login_id in active:
                if login_id in self.award_checks or waiting_for_signin and login_id not in restored:
                    continue
                prior = ledger.get(login_id, {})
                if prior.get('month') != month:
                    prior = {}
                if prior.get('status') in (4, 5, 6):
                    continue
                if prior.get('day') == day and not immediate:
                    group_progress = prior.get('status') in (1, 2) and latest_signin > prior['checked_at']
                    login_restored = login_id in restored and prior.get('login_required')
                    if not (group_progress or login_restored):
                        continue
                self.award_checks.add(login_id)
                self.scheduler.executor.submit(self._perform_award, login_id, month, day, prior.get('submitted', False))
                count += 1
            return count

    def _save_award(self, login_id, record):
        with self.scheduler.lock:
            updated = deepcopy(self.scheduler.data)
            updated.setdefault('team_awards', {})[login_id] = record.copy()
            self.scheduler._commit(updated)

    def _perform_award(self, login_id, month, day, check_only):
        record = {'month': month, 'day': day, 'status': None, 'submitted': check_only,
                  'checked_at': self.scheduler.now().isoformat(), 'prize': ''}

        def before_submit():
            if (not feature_running() or self.scheduler.quit.is_set()
                    or self.scheduler.now().strftime('%Y%m') != month):
                raise CheckinTaskError('功能已关闭或已跨月，本次未提交领奖', 409)
            record['submitted'] = True
            self._save_award(login_id, record)

        try:
            if not feature_running() or self.scheduler.now().strftime('%Y%m') != month:
                return
            with self._client(login_id, joining=True) as client:
                try:
                    status, prize = client.team_award(before_submit=before_submit, check_only=check_only)
                    record.update(status=status, prize=prize, submitted=check_only and status not in (4, 5, 6))
                except CheckinError as error:
                    unknown = record['submitted'] and (error.uncertain or error.code == 'UPSTREAM_FORMAT')
                    record.update(submitted=check_only or unknown, login_required=error.status == 401,
                                  message=str(error))
                self._save_award(login_id, record)
        except LoginStorageError as error:
            with self.scheduler.lock:
                self.scheduler.warning = str(error)
        finally:
            with self.scheduler.lock:
                self.award_checks.discard(login_id)

    def preview(self):
        with self.lock:
            self.plan = None
            now = self.scheduler.now()
            month = now.strftime('%Y%m')
            with self.scheduler.lock:
                if self.scheduler.load_error:
                    raise CheckinTaskError(self.scheduler.load_error, 503)
                observations = deepcopy(self.scheduler.data['observations'])
                joins = self.scheduler.data.get('team_joins', {})
            cohorts, join_only, open_teams = defaultdict(list), defaultdict(list), defaultdict(list)
            accounts = []
            for account in self.scheduler.accounts():
                login_id = account['id']
                observed = observations.get(login_id, {})
                row = {'id': login_id, 'name': account.get('name') or account['phone_masked'],
                       'phone_masked': account['phone_masked'],
                       'month_count': observed.get('month_count') if observed.get('date', '')[:6] == month else None}
                accounts.append(row)
                if account['status'] != 'active':
                    row['message'] = '共享账号需要恢复登录'
                    continue
                try:
                    state, token = self._query(login_id, row['phone_masked'])
                except CheckinError as error:
                    row['message'] = str(error)
                    continue
                row.update(state)
                if state['status'] not in (0, 1):
                    continue
                rules = state['minimum'], state['maximum'], state['goal']
                if state['status'] == 1 and token:
                    open_teams[rules].append(row)
                if state['status'] != 0:
                    continue
                prior = joins.get(login_id, {})
                if prior.get('month') == month and prior.get('state') in ('submitted', 'joined'):
                    row['message'] = '已有入团提交，等待官方状态确认'
                elif not token:
                    row['message'] = '官方未返回可用的组团邀请'
                    join_only[rules].append(row)
                else:
                    cohorts[rules].append(row)
            groups = []
            for rules in dict.fromkeys([*cohorts, *join_only]):
                minimum, maximum, goal = rules
                teams, leftover = _group_rows(cohorts[rules], minimum, maximum, goal)
                groups.extend({'accounts': team, 'minimum': minimum, 'maximum': maximum, 'goal': goal}
                              for team in teams)
                leftover.extend(join_only[rules])
                for leader in open_teams[rules]:
                    members, signed = [], leader['signed']
                    slots = maximum - leader['members']
                    while leftover and len(members) < slots and signed < goal:
                        member = leftover.pop(0)
                        members.append(member)
                        member['message'] = f'可加入 {leader["name"]} 的团队，等待确认'
                        if member['month_count'] is None:
                            break
                        signed += member['month_count']
                    if members:
                        groups.append({'accounts': [leader, *members], 'minimum': minimum, 'maximum': maximum,
                                       'goal': goal, 'existing': True})
                for row in leftover:
                    if row not in join_only[rules]:
                        row['message'] = f'暂无可加入的团队，另组至少需要 {minimum} 个未组队账号'
            plan_id = token_urlsafe(18) if groups else None
            if plan_id:
                self.plan = (plan_id, month, self.scheduler.now() + timedelta(minutes=10), groups)
            return {'plan_id': plan_id, 'groups': groups, 'accounts': accounts}

    def _record(self, login_id, month, state):
        with self.scheduler.lock:
            updated = deepcopy(self.scheduler.data)
            ledger = updated.setdefault('team_joins', {})
            ledger[login_id] = {'month': month, 'state': state}
            self.scheduler._commit(updated)

    def apply(self, plan_id):
        with self.lock:
            now = self.scheduler.now()
            if (self.plan is None or self.plan[0] != plan_id or self.plan[1] != now.strftime('%Y%m')
                    or self.plan[2] <= now):
                raise CheckinTaskError('组队预览已失效，请重新查询并预览。', 409)
            _, month, _, groups = self.plan
            self.plan = None
            reports = []
            ready = set()
            for group in groups:
                leader, *members = group['accounts']
                existing = group.get('existing', False)
                report = {'leader': leader, 'members': [], 'goal': group['goal'], 'planned_count': len(members),
                          'existing': existing}
                reports.append(report)
                try:
                    current, token = self._query(leader['id'], leader['phone_masked'])
                except CheckinError as error:
                    report['message'] = str(error)
                    continue
                if (current['status'] != (1 if existing else 0) or not token
                        or any(current[key] != group[key] for key in ('minimum', 'maximum', 'goal'))
                        or existing and len(members) > current['maximum'] - current['members']):
                    report['message'] = ('目标团队已达标、满员或邀请失效，请重新预览。' if existing
                                         else '团长资格或规则已变化，请重新预览。')
                    continue
                for member in members:
                    login_id = member['id']
                    submitted = False

                    def before_submit():
                        nonlocal submitted
                        if not feature_running() or self.scheduler.now().strftime('%Y%m') != month:
                            raise CheckinTaskError('功能已关闭或已跨月，组队已停止。', 409)
                        self._record(login_id, month, 'submitted')
                        submitted = True

                    result = {'account': member, 'joined': False}
                    report['members'].append(result)
                    with self._client(login_id, joining=True) as client:
                        try:
                            client.join_team(token, before_submit=before_submit)
                            self._record(login_id, month, 'joined')
                            result.update(joined=True, message='官方确认已加入')
                        except CheckinError as error:
                            result['message'] = str(error)
                            if submitted and not error.uncertain:
                                self._record(login_id, month, 'rejected')
                            if error.uncertain or error.code in ('FULL_GROUP', 'FINISH_GROUP', 'TOKEN_ERROR'):
                                break
                try:
                    report['status'], _ = self._query(leader['id'])
                    if report['status']['status'] == 3:
                        ready.add(leader['id'])
                        ready.update(row['account']['id'] for row in report['members'] if row['joined'])
                except CheckinError as error:
                    report['message'] = str(error)
            if ready:
                self.queue_awards(ready, immediate=True)
            return {'groups': reports}
