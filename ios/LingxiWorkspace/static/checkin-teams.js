(() => {
  'use strict';
  const preview = $('checkin-team-preview');
  const join = $('checkin-team-join');
  const award = $('checkin-team-award');
  const feedback = $('checkin-team-feedback');
  const groups = $('checkin-team-groups');
  const accountRows = $('checkin-team-account-rows');
  const accountDetails = $('checkin-team-accounts');
  let planId = null;

  function teamCard(number, leader, members, detail, existing) {
    const card = node('li', 'checkin-team-card');
    card.append(node('h3', '', `第 ${number} 团 · ${existing ? '加入现有团 · ' : ''}团长 ${leader.name}`),
      node('p', 'checkin-team-note', detail));
    if (members.length) {
      const list = node('ul', 'checkin-team-members');
      list.append(...members.map(text => node('li', '', text)));
      card.append(list);
    }
    return card;
  }

  preview.addEventListener('click', async () => {
    preview.disabled = join.disabled = true;
    join.hidden = true;
    planId = null;
    feedback.textContent = '正在查询各共享账号的官方组团状态…';
    try {
      const result = await api('/api/checkin/teams/preview', {});
      planId = result.plan_id;
      groups.replaceChildren(...result.groups.map((group, index) => {
        const [leader, ...members] = group.accounts;
        if (group.existing) {
          return teamCard(index + 1, leader, members.map(account => account.name),
            `现有 ${leader.members} 人 · ${leader.signed} / ${group.goal} 次 · 拟加入 ${members.length} 人`, true);
        }
        const known = group.accounts.every(account => account.month_count != null);
        const estimated = known ? ` · 预计合计 ${group.accounts.reduce((sum, account) => sum + account.month_count, 0)} 次` : '';
        return teamCard(index + 1, leader, members.map(account => account.name),
          `${group.accounts.length} 人 · 目标 ${group.goal} 次${estimated}`);
      }));
      accountRows.replaceChildren(...result.accounts.map(account => {
        const row = node('li');
        const progress = account.members ? ` · ${account.members} 人 · ${account.signed} / ${account.goal} 次` : '';
        const prize = account.award ? ' · ' + account.award : '';
        const closed = [3, 4, 5, 6].includes(account.status) ? ' · 达标后停止入团' : '';
        row.append(node('strong', '', account.name), node('span', '', account.message + progress + prize + closed));
        return row;
      }));
      groups.hidden = !result.groups.length;
      accountDetails.hidden = !result.accounts.length;
      join.hidden = !planId;
      feedback.textContent = planId
        ? `已排出 ${result.groups.length} 团。确认后将向官方提交入团，本月内不能退团或换人。`
        : '当前没有可提交的组队方案。可在下方查看各账号的状态。';
    } catch (error) {
      feedback.textContent = error.message;
    } finally {
      preview.disabled = join.disabled = false;
    }
  });

  award?.addEventListener('click', async () => {
    award.disabled = true;
    try {
      const result = await api('/api/checkin/teams/awards', {});
      feedback.textContent = result.queued ? `已安排 ${result.queued} 个账号检查，符合资格时自动领取。`
        : '当前没有需要另行检查的账号，请查看上方领取进度。';
      await refresh();
    } catch (error) {
      feedback.textContent = error.message;
    } finally {
      award.disabled = false;
    }
  });

  join.addEventListener('click', async () => {
    preview.disabled = join.disabled = true;
    feedback.textContent = '正在按预览名单组队，请等待官方结果…';
    try {
      const result = await api('/api/checkin/teams/join', {plan_id: planId});
      groups.replaceChildren(...result.groups.map((group, index) => {
        const members = group.members.map(item => `${item.account.name} · ${item.message}`);
        const skipped = group.planned_count - group.members.length;
        if (skipped) members.push(`${skipped} 个账号未提交`);
        const current = group.status;
        const detail = current?.members ? `${current.message} · ${current.members} 人 · ${current.signed} / ${current.goal} 次`
          : group.message || current?.message || '等待官方更新进度';
        return teamCard(index + 1, group.leader, members, detail, group.existing);
      }));
      const joined = result.groups.reduce((sum, group) => sum + group.members.filter(member => member.joined).length, 0);
      feedback.textContent = `官方确认 ${joined} 个队员入团。可再次查询查看最新团队进度。`;
      accountDetails.hidden = true;
    } catch (error) {
      feedback.textContent = error.message;
    } finally {
      planId = null;
      join.hidden = true;
      preview.disabled = join.disabled = false;
    }
  });
})();
