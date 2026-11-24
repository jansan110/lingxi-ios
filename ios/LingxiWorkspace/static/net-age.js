(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const accountList = $('age-accounts');
  const dialog = $('age-confirm');
  const rows = new Map([...document.querySelectorAll('[data-age-group]')].map(row => [row.dataset.ageGroup, row]));
  const accountStates = {active:'已登录', login_required:'需重新登录', restoring:'正在恢复', retry:'等待重连'};
  let sharedAccounts = [], currentAccount, needsRelogin = false;
  let availableCounts = {};
  let model, sevenDay, surprise, surpriseError = '', selected, operation, queryVersion = 0, accountVersion = 0, submitting = false, drawTimer, surpriseTimer;
  const surpriseChecks = new Set();

  async function api(path, values) {
    const response = await fetch(path, values
      ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(values)}
      : {cache:'no-store'});
    if (response.status === 404 && path === '/api/seven-day/status') return null;
    const data = await response.json();
    if (!response.ok) throw Object.assign(new Error(data.error || '本地服务请求失败'),
      {code:data.code, trace_id:data.trace_id, operation_active:data.operation_active});
    return data;
  }

  function message(id, value) {
    $(id).textContent = value;
    $(id).hidden = !value;
  }

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text) element.textContent = text;
    return element;
  }

  function accountLabel(account) {
    return account.name && account.name !== account.phone_masked ? `${account.name} · ${account.phone_masked}` : account.phone_masked;
  }

  function renderAccounts() {
    $('age-account-count').textContent = sharedAccounts.length;
    message('age-account-empty', sharedAccounts.length ? '' : '请在账号管理中添加共享账号。');
    accountList.replaceChildren(...sharedAccounts.map(account => {
      const active = account.id === currentAccount?.id;
      const card = node('button', 'coupons-account' + (active ? ' selected' : ''));
      card.type = 'button';
      card.dataset.loginId = account.id;
      card.setAttribute('aria-pressed', String(active));
      const label = node('span', 'coupons-account-label');
      label.append(node('strong', '', account.name || account.phone_masked), node('span', 'coupons-account-reminder'),
        node('span', 'coupons-account-state ' + (account.status === 'active' ? 'active' : 'attention'),
          accountStates[account.status] || '暂不可用'));
      card.append(label, node('small', '', account.phone_masked));
      card.addEventListener('click', () => {
        currentAccount = account;
        for (const other of accountList.children) {
          const pressed = other === card;
          other.classList.toggle('selected', pressed);
          other.setAttribute('aria-pressed', String(pressed));
        }
        return loadStatus();
      });
      return card;
    }));
    renderAccountCounts();
  }

  function renderAccountCounts() {
    for (const card of accountList.children) {
      const count = availableCounts[card.dataset.loginId] || 0;
      const badge = card.querySelector('.coupons-account-reminder');
      badge.hidden = count === 0;
      badge.textContent = String(count);
      badge.title = `${count} 项网龄权益可领取`;
      badge.setAttribute('aria-label', badge.title);
    }
  }

  async function loadAccountCounts(version) {
    const queue = sharedAccounts.filter(account => account.status === 'active' && account.id !== currentAccount?.id);
    let changed = false;
    await Promise.all(Array.from({length:Math.min(3, queue.length)}, async () => {
      while (queue.length && version === accountVersion) {
        const {id} = queue.shift();
        if (id === currentAccount?.id) continue;
        try {
          const {count} = await api('/api/net-age/availability?id='+encodeURIComponent(id));
          if (version === accountVersion && id !== currentAccount?.id && count !== null && availableCounts[id] !== count) {
            availableCounts[id] = count;
            renderAccountCounts();
            changed = true;
          }
        } catch { /* A badge can wait for the next official confirmation. */ }
      }
    }));
    if (changed) window.dispatchEvent(new Event('net-age-counts-changed'));
  }

  function action(area, text, handler, disabled = false) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'secondary'; button.textContent = text; button.disabled = disabled;
    button.addEventListener('click', handler);
    area.append(button);
  }

  async function checkSurprise() {
    const {id} = currentAccount, version = queryVersion;
    if (submitting || surpriseChecks.has(id) || surprise?.accounts.find(account => account.id === id)?.running) return;
    surpriseChecks.add(id);
    surpriseError = '';
    render();
    try {
      await api('/api/net-age/surprise/auto', {id});
      if (version !== queryVersion) return;
      await pollSurprise(id, version);
    } catch (error) {
      if (version === queryVersion) surpriseError = error.message;
    } finally {
      surpriseChecks.delete(id);
      render();
    }
  }

  async function pollSurprise(id, version = queryVersion) {
    if (version !== queryVersion || currentAccount?.id !== id) return;
    clearTimeout(surpriseTimer);
    try {
      const data = await api('/api/net-age/surprise/auto');
      if (version !== queryVersion || currentAccount?.id !== id) return;
      surprise = data;
      surpriseError = '';
      if (data.accounts.find(account => account.id === id)?.running) {
        surpriseTimer = setTimeout(() => pollSurprise(id, version), 1500);
      }
    } catch (error) {
      if (version === queryVersion) surpriseError = error.message;
    }
    render();
  }

  function showChoices(group, retry) {
    operation = null;
    $('age-confirm-title').textContent = group === 'seven' ? '确认参加今日抽奖' : rows.get(group).querySelector('h3').textContent;
    $('age-options').hidden = group === 'seven' || !!selected.choice;
    $('age-verification').hidden = true;
    $('age-slider-section').hidden = group !== 'member';
    $('age-code').value = ''; $('age-code').required = false;
    $('age-send-sms').textContent = '获取验证码';
    $('age-submit').textContent = group === 'seven' ? '确认抽奖' : retry ? '确认重新领取' : '确认领取';
  }

  function confirm(group, offers, retry = false) {
    if (submitting) return;
    selected = {id:currentAccount.id, group};
    if (retry) selected.retry = true;
    if (offers.length === 1) selected.choice = offers[0].id;
    showChoices(group, retry);
    $('age-confirm-account').textContent = accountLabel(currentAccount);
    const choices = $('age-options'); choices.replaceChildren();
    if (offers.length > 1) offers.forEach((offer, index) => {
      const label = document.createElement('label'), input = document.createElement('input');
      input.type = 'radio'; input.name = 'age-choice'; input.value = offer.id; input.required = true; input.checked = index === 0;
      label.append(input, offer.name); choices.append(label);
    });
    const memberRetry = group === 'member' && retry;
    let note = '';
    if (memberRetry) note = '本月暂未查到官方领取订单。请确认本月尚未领取，再继续领取。';
    else if (retry && group === 'birthday') note = '请确认今年尚未领取生日礼，再继续领取。';
    else if (retry) note = '重新领取前会核对本月的官方流量券订单。';
    message('age-confirm-note', note);
    dialog.showModal();
    if (offers.length === 1) {
      $('age-confirm-title').textContent = offers[0].name;
      return submitClaim();
    }
  }

  function render() {
    $('age-current-account').textContent = currentAccount ? accountLabel(currentAccount) : '尚未选择账号';
    message('age-profile', model ? [model.age, model.level, model.birthday && '网龄生日 '+model.birthday].filter(Boolean).join(' · ') : '');
    const relogin = $('age-relogin');
    relogin.hidden = !currentAccount || (currentAccount.status !== 'login_required' && !needsRelogin);
    relogin.dataset.accountRelogin = currentAccount?.id || '';
    const unavailable = !currentAccount ? '待选账号' : needsRelogin ? '需重新登录'
      : currentAccount.status !== 'active' && !model ? accountStates[currentAccount.status] || '暂不可用' : '';
    for (const [group, row] of rows) {
      const status = row.querySelector('[data-age-status]');
      const detail = row.querySelector('[data-age-detail]');
      const area = row.querySelector('[data-age-actions]');
      area.replaceChildren();
      if (group === 'surprise' && currentAccount) {
        const account = surprise?.accounts.find(item => item.id === currentAccount.id);
        const checking = surpriseChecks.has(currentAccount.id) || account?.running;
        const draws = (account?.draws || []).filter(item => item.confirmed);
        const wins = draws.filter(item => item.won);
        const prizes = (account?.prizes || []).slice(0, 3).map(item => `${item.name}（${item.date}）`).join('、');
        status.textContent = surprise?.enabled === false ? '自动任务已停用' : surprise?.active === false ? '活动已结束'
          : checking ? '正在核对并抽取' : account?.uncertain ? '结果待核对' : account?.state === 'failed' ? '自动抽奖暂停'
          : Number.isInteger(account?.remaining) ? `剩余 ${account.remaining} 次` : '待自动检查';
        detail.textContent = ['后台每日检查全部有效账号，有次数就抽', surpriseError || surprise?.warning || account?.message,
          draws.length ? `今日已核对 ${draws.length} 次抽奖${wins.length ? '，抽中 '+wins.map(item => item.name).join('、') : '，未中奖'}` : '',
          wins.some(item => item.needs_address) ? '实物奖品请在 48 小时内填写收货信息' : '',
          prizes ? '历史奖品：'+prizes : ''].filter(Boolean).join(' · ');
        action(area, checking ? '正在处理…' : '立即检查', checkSurprise,
          checking || submitting || !surprise || !surprise.enabled || !surprise.active);
        continue;
      }
      if (unavailable) {
        status.textContent = unavailable;
        detail.textContent = '';
        continue;
      }
      if (group === 'seven') {
        const account = sevenDay?.accounts.find(item => item.id === currentAccount.id);
        const value = account?.observation;
        if (sevenDay) {
          const link = document.createElement('a'); link.href = '/seven-day'; link.textContent = '打卡详情'; area.append(link);
        }
        status.textContent = sevenDay === undefined ? '待查询' : !sevenDay ? '活动未启用' : !sevenDay.campaign.active ? (sevenDay.campaign.ended ? '活动已结束' : '活动未开放') : account?.running ? '正在核对'
          : value?.chances === null || value?.chances === undefined ? '次数待查询' : value.chances ? '今日可抽 '+value.chances+' 次' : '今日无剩余次数';
        detail.textContent = account?.observation?.draw_prize || account?.message || '每个符合资格的账号每日一次';
        if (sevenDay?.campaign.active && account?.status === 'active') {
          action(area, '查询次数', () => sevenRun('refresh'), account.running || submitting);
          action(area, '抽奖', () => confirm(group, []), !value?.chances || account.running || submitting);
        }
        continue;
      }
      const value = model?.rows[group];
      const offers = value?.offers || [];
      const received = offers.filter(offer => offer.received).map(offer => offer.name);
      const choices = offers.filter(offer => !offer.received);
      const last = value?.last_received;
      status.textContent = value?.status || '待查询';
      detail.textContent = [value?.description, received.length ? '已领 '+received.join('、') : '',
        last ? `最近领取 ${last.name}（${last.date}）` : ''].filter(Boolean).join(' · ');
      if (value?.recheck) action(area, '核对结果', loadStatus, submitting);
      if (value?.retry_available && choices.length) {
        const label = choices.length === 1 || group === 'flow' ? '重新领取' : group === 'member' ? '选择领取' : '重新准备';
        action(area, label, () => confirm(group, choices, true), submitting);
      }
      if (value?.available && choices.length) action(area, choices.length === 1 ? '领取' : '选择领取',
        () => confirm(group, choices), submitting);
    }
  }

  async function loadStatus() {
    const account = currentAccount, version = ++queryVersion;
    model = null; sevenDay = undefined; surprise = undefined; surpriseError = ''; needsRelogin = false;
    clearTimeout(drawTimer);
    clearTimeout(surpriseTimer);
    render();
    if (!account) {
      message('age-feedback', '');
      return;
    }
    const {id} = account;
    if (rows.has('surprise')) void pollSurprise(id, version);
    message('age-feedback', '正在查询官方权益…');
    void api('/api/seven-day/status').then(campaign => {
      if (version === queryVersion) {
        sevenDay = campaign;
        render();
        if (campaign?.campaign.active) {
          const account = campaign.accounts.find(item => item.id === id);
          if (account?.running) pollSeven(id, false);
          else if (account?.status === 'active') sevenRun('refresh', id, false);
        }
      }
    }).catch(() => {});
    try {
      const age = await api('/api/net-age/status?id='+encodeURIComponent(id));
      if (version !== queryVersion) return;
      model = age;
      availableCounts[id] = model.available_count || 0;
      renderAccountCounts();
      window.dispatchEvent(new Event('net-age-counts-changed'));
      message('age-feedback', '');
    } catch (error) {
      if (version !== queryVersion) return;
      needsRelogin = ['APP_LOGIN_REQUIRED','ACTIVITY_LOGIN_REQUIRED'].includes(error.code);
      message('age-feedback', error.message);
    }
    render();
  }

  async function loadAccounts() {
    const version = ++accountVersion;
    try {
      const data = await api('/api/net-age/accounts');
      if (version !== accountVersion) return;
      const previous = currentAccount?.id;
      sharedAccounts = data.accounts;
      availableCounts = data.available_counts || {};
      currentAccount = sharedAccounts.find(account => account.id === previous)
        || sharedAccounts.find(account => account.status === 'active') || sharedAccounts[0];
      renderAccounts();
      void loadAccountCounts(version);
      await loadStatus();
    } catch (error) {
      if (version !== accountVersion) return;
      if (!sharedAccounts.length) message('age-account-empty', '账号读取失败，请刷新重试。');
      message('age-feedback', error.message);
    }
  }

  async function pollSeven(id, announce = true, version = queryVersion) {
    if (version !== queryVersion || currentAccount?.id !== id) return;
    clearTimeout(drawTimer);
    try {
      const result = await api('/api/seven-day/status');
      if (version !== queryVersion || currentAccount?.id !== id) return;
      sevenDay = result; render();
      if (sevenDay?.accounts.find(account => account.id === id)?.running) {
        drawTimer = setTimeout(() => pollSeven(id, announce, version), 1200);
      }
    } catch (error) { if (announce && version === queryVersion) message('age-feedback', error.message); }
  }

  async function sevenRun(kind, id = currentAccount.id, announce = true) {
    const version = queryVersion;
    try {
      const data = await api('/api/seven-day/run', {id, kind});
      if (version !== queryVersion || currentAccount?.id !== id) return;
      if (announce) message('age-feedback', data.message);
      await pollSeven(id, announce, version);
    } catch (error) { if (announce && version === queryVersion) message('age-feedback', error.message); }
  }

  async function verify(action, value = {}) {
    return api('/api/net-age/verification/'+action, {id:selected.id, operation:operation.operation, ...value});
  }

  function setBusy(busy) {
    submitting = busy;
    $('age-submit').disabled = busy || !!(operation && !operation.sms);
    $('age-send-sms').disabled = busy;
  }

  function verificationNote(text) {
    message('age-confirm-note', [operation?.notice, text].filter(Boolean).join(' '));
  }

  async function verificationStep(run) {
    if (submitting) return;
    setBusy(true);
    verificationNote();
    try { await run(); }
    catch (error) { verificationNote(error.message); }
    finally { setBusy(false); }
  }

  async function sendSms() {
    if (operation.needs_slider) {
      verificationNote('正在自动完成图形验证…');
      await verify('auto-slider');
    }
    verificationNote('正在发送验证码…');
    const data = await verify('sms');
    operation.sms = true;
    $('age-send-sms').textContent = '重新获取验证码';
    verificationNote(data.message);
    $('age-code').focus();
  }

  function showVerification(data) {
    operation = data;
    $('age-options').hidden = true;
    $('age-confirm-title').textContent = data.offer;
    $('age-verification').hidden = false;
    $('age-slider-section').hidden = !data.needs_slider;
    $('age-sms-section').hidden = false;
    $('age-code').required = true;
    $('age-submit').textContent = '确认领取';
    verificationNote();
  }

  $('age-send-sms').addEventListener('click', () => verificationStep(async () => {
    operation.sms=false;
    $('age-code').value='';
    await sendSms();
  }));
  async function submitClaim() {
    if (submitting) return;
    const group = selected.group;
    setBusy(true);
    try {
      if (group === 'seven') {
        dialog.close(); await sevenRun('draw', selected.id);
      } else {
        if (operation) verificationNote(operation.needs_slider ? '正在自动验证并提交领取…' : '正在提交领取…');
        const data = operation ? await verify('submit', {code:$('age-code').value})
          : await api('/api/net-age/claim', {...selected,
              choice:selected.choice || new FormData($('age-confirm-form')).get('age-choice')});
        if (data.operation) {
          showVerification(data);
          await sendSms();
          return;
        }
        if (group === 'flow' && data.code === '10') {
          selected.retry = true;
          showChoices(group, true);
          verificationNote(data.message);
          const flow = model?.rows.flow;
          if (flow && selected.id === currentAccount?.id) {
            Object.assign(flow, {status:data.message, description:'', available:false, retry_available:true, recheck:true});
          }
          return;
        }
        operation = null;
        dialog.close();
        if (selected.id === currentAccount?.id) {
          const birthday = model?.rows.birthday;
          if (group === 'birthday' && data.code === '10' && birthday) {
            Object.assign(birthday, {status:'上次领取失败', description:data.message || '官方返回领取失败（10）',
              available:false, retry_available:true, recheck:true});
          } else {
            await loadStatus();
          }
          if (selected.id === currentAccount?.id) message('age-feedback', data.message);
        }
      }
    } catch (error) {
      if (operation && (error.operation_active === false || group === 'birthday' && error.code === 'GDT_REJECTED')) {
        showChoices(group, selected.retry);
      }
      verificationNote([error.message, error.trace_id && '追踪号 '+error.trace_id].filter(Boolean).join(' · '));
    }
    finally {
      if (group === 'member' || group === 'birthday') {
        window.dispatchEvent(new Event('net-age-counts-changed'));
      }
      setBusy(false); render();
    }
  }
  $('age-confirm-form').addEventListener('submit', event => {
    event.preventDefault();
    return submitClaim();
  });
  $('age-cancel').addEventListener('click', () => { if (!submitting) dialog.close(); });
  dialog.addEventListener('cancel', event => { if (submitting) event.preventDefault(); });
  dialog.addEventListener('close', () => {
    if (operation) verify('cancel').catch(error => message('age-feedback', error.message));
    operation = null;
  });
  $('age-refresh').addEventListener('click', loadAccounts);
  window.addEventListener('accounts-changed', loadAccounts);
  window.addEventListener('net-age-counts-updated', event => {
    availableCounts = event.detail;
    renderAccountCounts();
  });
  loadAccounts();
})();
