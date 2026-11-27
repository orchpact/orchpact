export async function showAccount(ui) {
  const {content:root, params, globalApi, current, signal, refreshIdentity, openWorkspace} = ui;
  root.replaceChildren(document.querySelector('#account-view').content.cloneNode(true));
  const api = (path, method = 'GET', value, headers) => globalApi(path,{method,value,headers,signal:method === 'GET' ? signal : undefined});
  const $ = selector => root.querySelector(selector);
  const encode = encodeURIComponent;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const amount = value => Number(value).toLocaleString('zh-CN',{maximumFractionDigits:6});
  const date = value => new Date(value * 1000).toLocaleString('zh-CN');
  const fields = form => Object.fromEntries(new FormData(form));
  const creditDialog = $('#credit-dialog');
  const inviteDialog = $('#invite-dialog');
  const memberPanel = $('#members');
  let me = ui.me, selected, statusTimer;
  let loadVersion = 0, memberVersion = 0, creditVersion = 0;

  function workspaceLink(id) {
    const url = new URL(location.href);
    url.searchParams.set('workspace', id);
    url.hash = 'overview';
    return url.pathname + url.search + url.hash;
  }

  function tell(message, error = false) {
    if (!current()) return;
    const region = root.querySelector('dialog[open] .account-status') || $('#message');
    clearTimeout(statusTimer);
    region.classList.toggle('error', error);
    if (region.id !== 'message') $('#message').textContent = '';
    region.textContent = message;
    if (!error) statusTimer = setTimeout(() => { region.textContent = ''; }, 6000);
  }

  function changed() {
    localStorage.setItem('account.changed', crypto.randomUUID());
  }

  async function run(action, button) {
    if (button?.disabled) return;
    if (button) button.disabled = true;
    try {
      await action();
    } catch (error) {
      if (error.name !== 'AbortError') tell(error.message, true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  function renderMemberButtons() {
    $('#spaces').querySelectorAll('[data-members]').forEach(button => {
      button.setAttribute('aria-expanded', String(button.dataset.members === selected));
    });
  }

  async function showMembers(id) {
    if (!current()) return;
    const version = ++memberVersion;
    const space = me.workspaces.find(workspace => workspace.id === id);
    if (!space) {
      selected = null;
      memberPanel.hidden = true;
      renderMemberButtons();
      return;
    }
    selected = id;
    renderMemberButtons();
    const canManage = me.admin || space.owner_id === me.user_id;
    memberPanel.hidden = false;
    memberPanel.innerHTML = '<p class="muted">正在读取成员…</p>';
    const path = 'workspaces/' + encode(id);
    try {
      const [rows, invitations] = await Promise.all([
        api(path+'/members'),
        canManage && !space.personal ? api(path+'/invitations') : [],
      ]);
      if (version !== memberVersion || !current()) return;
      const active = invitations.filter(invite => !invite.revoked && !invite.used_by && invite.expires * 1000 > Date.now());
      memberPanel.innerHTML = '<h3>'+esc(space.name)+' · 成员</h3><div class="account-list">'
        + rows.map(user => '<div class="account-row"><span class="row-label">'+esc(user.username)
          + ' <span class="badge">'+(user.role==='owner'?'所有者':'成员')+'</span></span>'
          + (user.role !== 'owner' && (canManage || user.id === me.user_id)
            ? '<button type="button" data-remove="'+esc(user.id)+'">'+(user.id===me.user_id?'退出空间':'移除成员')+'</button>' : '')
          + '</div>').join('') + '</div>'
        + (active.length ? '<h4>待接受邀请</h4><div class="account-list">'
          + active.map(invite => '<div class="account-row"><span class="row-label">有效至 '+esc(date(invite.expires))
            + '</span><button type="button" data-revoke="'+esc(invite.id)+'">撤销邀请</button></div>').join('')
          + '</div>' : '');
    } catch (error) {
      if (version !== memberVersion || !current()) return;
      memberPanel.replaceChildren();
      memberPanel.hidden = true;
      selected = null;
      renderMemberButtons();
      throw error;
    }
  }

  function renderSpaces(owners = new Map()) {
    $('#spaces').innerHTML = me.workspaces.map(space => {
      const owner = owners.get(space.owner_id);
      const personal = me.admin && space.id === me.workspace_id ? '管理空间'
        : owner ? '私人 · '+owner : '个人专用';
      const context = space.personal ? personal
        : space.role === 'owner' ? '协作 · 你是所有者' : me.admin ? '协作空间' : '协作 · 已加入';
      return '<div class="account-row"><div class="row-label"><strong>'+esc(space.name)
        + '</strong><small>'+esc(context)+'</small></div><div class="actions">'
        + '<a class="action-link" data-open-space="'+esc(space.id)+'" href="'+esc(workspaceLink(space.id))
        + '" aria-label="打开 '+esc(space.name)+'">打开</a>'
        + '<button type="button" data-members="'+esc(space.id)+'" aria-controls="members">成员</button>'
        + (!space.personal && (space.role === 'owner' || me.admin)
          ? '<button type="button" data-invite="'+esc(space.id)+'">邀请</button>' : '')
        + '</div></div>';
    }).join('') || '<p class="muted">暂无工作空间。</p>';
    renderMemberButtons();
  }

  function creditFacts(credits) {
    return '<dl class="credit-facts"><div><dt>总余额</dt><dd>'+amount(credits.balance)
      + '</dd></div><div><dt>调用预留</dt><dd>'+amount(credits.held)+'</dd></div></dl>';
  }

  function renderWallet() {
    $('#credit-summary').hidden = !me.credits;
    if (!me.credits) return;
    $('#wallet-metrics').innerHTML = '<small>可用积分</small><div class="number" id="credit-available">'
      + amount(me.credits.available) + '</div>' + creditFacts(me.credits);
  }

  async function load() {
    if (signal.aborted) return;
    const version = ++loadVersion;
    const session = await refreshIdentity(signal);
    if (version !== loadVersion || !current()) return;
    me = session;
    $('#admin-tools').hidden = !me.admin;
    renderWallet();
    renderSpaces();
    const active = selected && me.workspaces.some(space => space.id === selected);
    if (!active) {
      selected = null;
      memberVersion++;
      memberPanel.hidden = true;
    }
    if (me.admin) {
      const users = await api('admin/users');
      if (version !== loadVersion || !current()) return;
      renderSpaces(new Map(users.map(user => [user.id, user.username])));
      $('#users').innerHTML = users.map(user => '<div class="account-row"><div class="row-label"><strong>'
        + esc(user.username)+'</strong> <span class="badge">'+(user.disabled?'已停用':'正常')+'</span><small>可用 '
        + amount(user.credits.available)+' 积分</small></div><div class="actions">'
        + '<button type="button" data-credits="'+esc(user.id)+'" data-name="'+esc(user.username)+'">积分管理</button>'
        + (user.admin ? '' : '<button type="button" data-user="'+esc(user.id)+'" data-disabled="'+Number(!user.disabled)+'">'
          + (user.disabled?'启用账号':'停用账号')+'</button>')+'</div></div>').join('');
    }
  }

  function showInvitation(value, kind) {
    if (!current()) return;
    $('#invite-title').textContent = kind === 'account' ? '注册邀请码' : '空间邀请码';
    $('#invite-help').textContent = '邀请码只能使用一次，有效至 ' + date(Date.now()/1000 + value.expires_in) + '。';
    $('#invite-code').value = value.code;
    inviteDialog.querySelector('.account-status').textContent = '';
    inviteDialog.showModal();
  }

  async function showCredits(user, name) {
    const version = ++creditVersion;
    const path = me.admin ? 'admin/users/'+encode(user)+'/credits' : 'auth/credits';
    const account = await api(path);
    if (version !== creditVersion || !current()) return;
    $('#credit-title').textContent = name+' · 积分账户';
    const names = {reserve:'调用预留',settlement:'调用结算',adjustment:'管理员调整'};
    $('#credit-body').innerHTML = '<p><strong>可用 '+amount(account.credits.available)+' 积分</strong></p>'
      + creditFacts(account.credits)
      + (me.admin ? '<section class="credit-section"><h3>调整积分</h3><form id="credit-adjust" class="account-form">'
        + '<label>增减积分（负数扣减）<input name="delta" type="number" step="0.000001" min="-1000000000" max="1000000000" required></label>'
        + '<label>调整原因<input name="reason" maxlength="500" required></label><button type="submit">保存积分调整</button></form></section>' : '')
      + '<section class="credit-section"><h3>未结算调用</h3>'
      + (account.pending.map(item => '<div class="credit-entry"><p>'+esc(item.request_id)
        + ' · '+esc(item.status)+' · 预留 '+amount(item.reserved)+'</p>'
        + (me.admin && item.status !== 'started' ? '<form data-settle="'+esc(item.request_id)+'" class="account-form">'
          + '<label>核定扣除积分（0 表示全部释放）<input name="points" type="number" min="0" max="1000000000" step="0.000001" required></label>'
          + '<label>核定原因<input name="reason" maxlength="500" required></label><button type="submit">核定并结算</button></form>' : '')
        + '</div>').join('') || '<p class="muted">暂无未结算调用。</p>') + '</section>'
      + '<section class="credit-section"><h3>最近 100 条流水</h3>'
      + (account.ledger.map(item => '<div class="credit-entry"><strong>'+esc(names[item.kind] || item.kind)
        + ' · '+(item.delta>0?'+':'')+amount(item.delta)+' 积分</strong><p>'+esc(item.reason)+'</p>'
        + '<small>'+esc(date(item.created))+' · 余额 '+amount(item.balance)+' · 预留 '+amount(item.held)
        + (item.request_id ? '<br>调用 '+esc(item.request_id) : '')+'</small></div>').join('')
        || '<p class="muted">暂无积分流水。</p>')+'</section>';
    $('#credit-body').onsubmit = event => {
      event.preventDefault();
      const form = event.target;
      return run(async () => {
        const raw = fields(form);
        const adjusting = form.id === 'credit-adjust';
        if (adjusting) {
          const payload = {delta:Number(raw.delta),reason:raw.reason};
          const signature = JSON.stringify(payload);
          if (form.dataset.payload !== signature) {
            form.dataset.payload = signature;
            form.dataset.key = crypto.randomUUID();
          }
          await api(path,'POST',payload,{'Idempotency-Key':form.dataset.key});
        } else {
          await api('admin/credit-requests/'+encode(form.dataset.settle)+'/settle','POST',{points:Number(raw.points),reason:raw.reason});
        }
        await load();
        if (version === creditVersion && current()) {
          await showCredits(user,name);
          tell(adjusting ? '积分已调整' : '调用积分已核定');
        }
      }, event.submitter);
    };
    if (!creditDialog.open) {
      creditDialog.querySelector('.account-status').textContent = '';
      creditDialog.showModal();
    }
  }

  $('#create-form').onsubmit = event => {
    event.preventDefault();
    const form = event.currentTarget;
    return run(async () => {
      await api('workspaces','POST',fields(form));
      form.reset();
      $('#create-space').open = false;
      await load();
      tell('工作空间已创建');
    }, event.submitter);
  };
  $('#join-form').onsubmit = event => {
    event.preventDefault();
    const form = event.currentTarget;
    return run(async () => {
      await api('workspaces/join','POST',{code:fields(form).code.trim()});
      form.reset();
      $('#join-space').open = false;
      await load();
      tell('已加入协作空间');
    }, event.submitter);
  };

  $('#spaces').onclick = event => {
    const link = event.target.closest('[data-open-space]');
    if (link && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && event.button === 0) {
      event.preventDefault();
      openWorkspace(link.dataset.openSpace);
      return;
    }
    const button = event.target.closest('button');
    if (button?.dataset.members) {
      const id = button.dataset.members;
      if (id === selected) {
        memberVersion++;
        selected = null;
        memberPanel.hidden = true;
        renderMemberButtons();
      } else return run(() => showMembers(id));
    }
    if (button?.dataset.invite) return run(async () => {
      const id = button.dataset.invite;
      showInvitation(await api('workspaces/'+encode(id)+'/invitations','POST',{}),'workspace');
      await showMembers(id);
    }, button);
  };
  memberPanel.onclick = event => {
    const button = event.target.closest('button');
    if (!button || !selected) return;
    const id = selected;
    const path = 'workspaces/'+encode(id);
    if (button.dataset.remove) return run(async () => {
      await api(path+'/members/'+encode(button.dataset.remove),'DELETE');
      changed();
      await load();
      if (selected === id) await showMembers(id);
    }, button);
    if (button.dataset.revoke) return run(async () => {
      await api(path+'/invitations/'+encode(button.dataset.revoke),'DELETE');
      if (selected === id) await showMembers(id);
    }, button);
  };
  $('#invite-account').onclick = event => run(async () => {
    showInvitation(await api('admin/invitations','POST',{}),'account');
  }, event.currentTarget);
  $('#copy-code').onclick = event => run(async () => {
    await navigator.clipboard.writeText($('#invite-code').value);
    tell('邀请码已复制');
  }, event.currentTarget);
  $('#my-credits').onclick = event => run(() => showCredits(me.user_id,me.username), event.currentTarget);
  $('#users').onclick = event => {
    const button = event.target.closest('button');
    if (button?.dataset.credits) return run(() => showCredits(button.dataset.credits,button.dataset.name),button);
    if (button?.dataset.user) return run(async () => {
      await api('admin/users/'+encode(button.dataset.user),'PATCH',{disabled:button.dataset.disabled === '1'});
      await load();
    },button);
  };
  for (const dialog of [creditDialog,inviteDialog]) {
    dialog.querySelector('[data-close]').onclick = () => dialog.close();
  }
  inviteDialog.onclose = () => { $('#invite-code').value = ''; };
  creditDialog.onclose = () => { creditVersion++; $('#credit-body').replaceChildren(); };

  signal.addEventListener('abort', () => {
    clearTimeout(statusTimer);
    for (const dialog of [creditDialog,inviteDialog]) dialog.close();
  }, {once:true});

  await load();
  if (signal.aborted) return;
  const creditId = params.get('credits');
  if (creditId === 'mine' || creditId === me.user_id) await showCredits(me.user_id, me.username);
  else if (me.admin && creditId) {
    const entry = [...$('#users').querySelectorAll('[data-credits]')].find(button => button.dataset.credits === creditId);
    if (entry) await showCredits(creditId, entry.dataset.name);
    else tell('该账号不存在。', true);
  }
}
