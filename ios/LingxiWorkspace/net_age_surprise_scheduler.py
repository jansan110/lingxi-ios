"""Daily, durable scheduling of confirmed free net-age surprise chances."""
from copy import deepcopy
from datetime import datetime, timedelta
from threading import Event, RLock, Thread

from activities.coc_client import CocError
from feature_runtime import ThreadPoolExecutor, feature_running
from login_storage import LoginStorageError
from recharge.cmcc import SHANGHAI


SCOPE = 'net-age-surprise-scheduler'
END_AT = datetime(2026, 12, 31, 23, 59, 59, tzinfo=SHANGHAI)


class SurpriseScheduler:
    """Check every active shared account once daily, starting at Beijing 00:00.

    Daily attempts are committed before a worker is queued. Every official
    failure stops automatic work for that account/day; an actual restored
    login can resume an authentication failure once. Explicit manual runs
    request a new official check, while the service owns draw-intent safety.
    """
    def __init__(self, service, accounts, storage=None, now=None, executor=None):
        self.service, self.accounts, self.storage = service, accounts, storage
        self.now = now or (lambda: datetime.now(SHANGHAI))
        self.executor = executor or ThreadPoolExecutor(max_workers=3, thread_name_prefix='net-age-surprise')
        self.lock, self.quit, self.thread = RLock(), Event(), None
        self.running = set()
        self.feature_enabled = lambda: True
        self.warning = self.load_error = ''
        self.data = {'records': {}, 'readiness': {}, 'restored': {}}
        if storage is not None:
            try:
                saved = storage.load_activity_data(SCOPE)
                if saved is not None:
                    if (not isinstance(saved, dict)
                            or any(not isinstance(saved.get(key), dict) for key in self.data)):
                        raise LoginStorageError('惊喜盲盒自动任务记录格式无效，已保留原数据库。')
                    self.data = saved
                interrupted = deepcopy(self.data)
                changed = False
                for records in interrupted['records'].values():
                    for record in records.values():
                        if record.get('state') == 'running':
                            record.update(state='failed', failure_kind='uncertain', uncertain=True,
                                          code='SURPRISE_INTERRUPTED', finished_at=self.now().isoformat(),
                                          message='上次任务执行中断，结果待官方核对，今日不自动重复执行。')
                            changed = True
                if changed:
                    self._commit(interrupted)
            except LoginStorageError as error:
                self.warning = self.load_error = str(error)

    def _commit(self, updated):
        if self.load_error:
            raise LoginStorageError(self.load_error)
        if self.storage is not None:
            try:
                self.storage.save_activity_data(SCOPE, updated)
            except LoginStorageError as error:
                self.warning = self.load_error = str(error)
                raise
        self.data = updated

    def _available(self):
        if self.quit.is_set() or not self.feature_enabled() or not feature_running():
            raise CocError('网龄已关闭，惊喜盲盒自动任务已停止。', 'FEATURE_DISABLED', 423)
        if self.load_error:
            raise CocError(self.load_error, 'STORAGE_ERROR', 503)
        if self.now().astimezone(SHANGHAI) > END_AT:
            raise CocError('惊喜盲盒活动已结束。', 'ACTIVITY_CLOSED', 409)

    def _enqueue(self, login_id, *, manual=False):
        if (self.quit.is_set() or self.load_error or not self.feature_enabled() or not feature_running()
                or self.now().astimezone(SHANGHAI) > END_AT):
            return False
        account = next((account for account in self.accounts() if account['id'] == login_id), None)
        if account is None or (not manual and account['status'] != 'active') or self.service.busy_login(login_id):
            return False
        now = self.now().astimezone(SHANGHAI)
        day = now.date().isoformat()
        with self.lock:
            if login_id in self.running:
                return False
            old = self.data['records'].get(login_id, {}).get(day, {})
            restored = self.data['restored'].get(login_id) == day
            auth_retry = (restored and old.get('failure_kind') == 'authentication'
                          and not old.get('auth_retry_used'))
            if old and old.get('state') != 'deferred' and not manual and not auth_retry:
                if restored:
                    updated = deepcopy(self.data)
                    updated['restored'].pop(login_id, None)
                    self._commit(updated)
                return False
            updated = deepcopy(self.data)
            record = {**old, 'date': day, 'state': 'running', 'phase': 'queued',
                      'started_at': now.isoformat(), 'finished_at': None,
                      'attempt_count': old.get('attempt_count', 0) + 1,
                      'manual': manual, 'auth_retry_used': old.get('auth_retry_used', False) or auth_retry,
                      'failure_kind': '', 'uncertain': False, 'code': '',
                      'message': '正在核对官方抽奖次数与奖品记录。'}
            updated['records'].setdefault(login_id, {})[day] = record
            updated['restored'].pop(login_id, None)
            self._commit(updated)
            self.running.add(login_id)
        try:
            self.executor.submit(self._perform, login_id, day)
        except RuntimeError:
            with self.lock:
                self.running.discard(login_id)
                self._finish(login_id, day, state='deferred', code='QUEUE_STOPPED',
                             message='任务尚未开始，等待后台调度恢复。')
            raise
        return True

    def _finish(self, login_id, day, **fields):
        with self.lock:
            updated = deepcopy(self.data)
            record = updated['records'][login_id][day]
            if fields.get('state') == 'deferred':
                record['attempt_count'] = max(0, record['attempt_count'] - 1)
            record.update(fields, phase='finished', finished_at=self.now().astimezone(SHANGHAI).isoformat())
            self._commit(updated)

    def _perform(self, login_id, day):
        try:
            with self.lock:
                manual = self.data['records'][login_id][day]['manual']
            account = next((account for account in self.accounts() if account['id'] == login_id), None)
            if (self.quit.is_set() or not self.feature_enabled() or not feature_running()
                    or account is None or (not manual and account['status'] != 'active')
                    or self.service.busy_login(login_id) or self.now().astimezone(SHANGHAI).date().isoformat() != day):
                self._finish(login_id, day, state='deferred', code='NOT_STARTED',
                             message='任务尚未开始，等待账号或后台调度恢复。')
                return
            self._available()
            with self.lock:
                updated = deepcopy(self.data)
                updated['records'][login_id][day]['phase'] = 'working'
                self._commit(updated)
            result = self.service.auto_surprise(login_id)
            if (not isinstance(result, dict) or type(result.get('remaining')) is not int or result['remaining'] < 0
                    or not isinstance(result.get('draws'), list) or not isinstance(result.get('prizes'), list)):
                raise CocError('官方抽奖结果尚无法确认，请主动核对记录。', 'SURPRISE_RESULT', uncertain=True)
            result.update(self.service.surprise_progress(login_id))
            completed = result['remaining'] == 0
            self._finish(login_id, day, state='completed' if completed else 'failed',
                         failure_kind='' if completed else 'remaining', uncertain=False, code='',
                         remaining=result['remaining'], draws=deepcopy(result['draws']), prizes=deepcopy(result['prizes']),
                         message=result.get('message') or ('今日可用次数已全部核对并处理。' if completed
                                                         else '仍有官方可用次数，今日不自动重试，请主动核对。'))
        except CocError as error:
            code, text = str(error.code).upper(), str(error)
            protected_refresh = (code == 'APP_REFRESH_FAILED' and '正在' in text
                                 and any(word in text for word in ('验证', '支付', '用券')))
            if not error.uncertain and (code == 'ACCOUNT_BUSY' or protected_refresh):
                self._finish(login_id, day, state='deferred', failure_kind='', code=str(error.code),
                             uncertain=False, message=text)
                return
            if error.uncertain:
                kind = 'uncertain'
            elif ('INELIGIBLE' in code or 'NOT_ELIGIBLE' in code or '不符合' in text or '无活动资格' in text):
                kind = 'ineligible'
            elif error.status == 429 or 'RATE' in code or code == '-800' or '频繁' in text or '限流' in text:
                kind = 'rate_limited'
            elif any(part in code for part in ('VERIFY', 'VERIFICATION', 'SLIDER', 'CAPTCHA', 'SMS')):
                kind = 'verification_required'
            elif error.status == 401 or any(part in code for part in ('LOGIN', 'AUTH', 'SESSION', 'CREDENTIAL')):
                kind = 'authentication'
            else:
                kind = 'failed'
            self._finish(login_id, day, state='failed', failure_kind=kind, code=str(error.code),
                         uncertain=bool(error.uncertain), message=text, **self.service.surprise_progress(login_id))
        except LoginStorageError as error:
            self.warning = self.load_error = str(error)
        finally:
            with self.lock:
                self.running.discard(login_id)
                restored = (login_id in self.data['restored']
                            and self.data['records'][login_id][day]['state'] != 'deferred')
            if restored:
                self._enqueue(login_id)

    def tick(self):
        if (self.quit.is_set() or self.load_error or not self.feature_enabled() or not feature_running()
                or self.now().astimezone(SHANGHAI) > END_AT):
            return 0
        accounts = self.accounts()
        readiness = {account['id']: account['status'] for account in accounts}
        with self.lock:
            if readiness != self.data['readiness']:
                updated = deepcopy(self.data)
                for login_id, state in readiness.items():
                    previous = updated['readiness'].get(login_id)
                    if state == 'active' and previous not in (None, 'active'):
                        updated['restored'][login_id] = self.now().astimezone(SHANGHAI).date().isoformat()
                updated['readiness'] = readiness
                self._commit(updated)
        return sum(self._enqueue(account['id']) for account in accounts if account['status'] == 'active')

    def run(self, login_id):
        self._available()
        account = next((account for account in self.accounts() if account['id'] == login_id), None)
        if account is None:
            raise CocError('请选择共享账号池中的账号。', 'APP_LOGIN_REQUIRED', 401)
        if self.service.busy_login(login_id):
            raise CocError('该账号正在验证网龄权益，请先完成或取消。', 'ACCOUNT_BUSY', 409)
        queued = self._enqueue(login_id, manual=True)
        return {'queued': queued, 'message': '已安排核对并使用官方可用次数。' if queued else '该账号正在处理，请稍候。'}

    def login_restored(self, login_id):
        """Keep a real login notification even while that account is working."""
        if not any(account['id'] == login_id for account in self.accounts()):
            return False
        with self.lock:
            updated = deepcopy(self.data)
            updated['restored'][login_id] = self.now().astimezone(SHANGHAI).date().isoformat()
            self._commit(updated)
        return self._enqueue(login_id)

    def status(self):
        """Return local records; this never queues or queries an official API."""
        now = self.now().astimezone(SHANGHAI)
        day = now.date().isoformat()
        enabled = self.feature_enabled()
        active = now <= END_AT
        with self.lock:
            rows = []
            for account in self.accounts():
                days = self.data['records'].get(account['id'], {})
                current = deepcopy(days.get(day, {}))
                last = deepcopy(days[max(days)]) if days else {}
                attempted = current and current.get('state') != 'deferred'
                next_run = None
                if enabled and active and account['status'] == 'active':
                    next_run = ((now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                                if attempted else now)
                    if next_run > END_AT:
                        next_run = None
                row = {key: account.get(key, '') for key in ('id', 'name', 'phone_masked', 'status')}
                row.update(auto_enabled=True, running=account['id'] in self.running,
                           completed_today=current.get('state') == 'completed', state=current.get('state', 'pending'),
                           failure_kind=current.get('failure_kind', ''), uncertain=current.get('uncertain', False),
                           remaining=current.get('remaining'), draws=deepcopy(current.get('draws', [])),
                           prizes=deepcopy(current.get('prizes', [])), message=current.get('message', '等待今日核对'),
                           record=current, last_result=last, records=deepcopy(days),
                           next_run_at=next_run.isoformat() if next_run else None)
                rows.append(row)
            return {'enabled': enabled, 'active': active, 'start_time': '00:00', 'end_at': END_AT.isoformat(),
                    'server_time': now.isoformat(), 'accounts': rows, 'warning': self.warning}

    def start(self):
        if self.thread is None:
            self.thread = Thread(target=self._loop, daemon=True, name='net-age-surprise-clock')
            self.thread.start()

    def _loop(self):
        while not self.quit.is_set() and self.now().astimezone(SHANGHAI) <= END_AT:
            try:
                self.tick()
            except (CocError, LoginStorageError) as error:
                self.warning = str(error)
            self.quit.wait(20)

    def shutdown(self):
        self.quit.set()
        if self.thread is not None:
            self.thread.join(timeout=1)
        self.executor.shutdown(wait=False, cancel_futures=True)
