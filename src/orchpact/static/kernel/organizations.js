'use strict';

export const OrchpactOrganizations = (() => {
 const dialog = document.querySelector('#organization-editor');
 const form = dialog.querySelector('form');
 const fields = dialog.querySelector('[data-fields]');
 const error = dialog.querySelector('[data-error]');
 const save = form.querySelector('[type=submit]');
 const capabilityLabels = {
  text: '文本', json: '结构化输出', search: '搜索',
  image: '图像生成', image_reference: '参考图'
 };
 const teamCapabilities = ['text', 'json', 'search'];
 const modelCapabilities = capabilities => capabilities.filter(capability => !capability.startsWith('tool:'));

 function control(scope, name) {
  return scope.querySelector('[name="' + name + '"]');
 }

 function parentChoices(units, id) {
  const byId = new Map(units.map(unit => [unit.id, unit]));
  return units.filter(unit => {
   const seen = new Set(id ? [id] : []);
   let next = unit;
   while (next) {
    if (seen.has(next.id)) return false;
    seen.add(next.id);
    next = byId.get(next.parent_id);
   }
   return true;
  });
 }

 function modelOptions(select, models, caps, fallback, selected) {
  const options = [new Option(fallback, '')];
  for (const model of models) {
   if (modelCapabilities(caps).every(capability => model.capabilities.includes(capability))) {
    const label = model.label && model.label !== model.id ? model.label + ' · ' + model.id : model.id;
    options.push(new Option(label, model.id));
   }
  }
  if (selected && !options.some(option => option.value === selected)) {
   options.push(new Option(selected + '（不可用或不匹配所选能力）', selected));
  }
  select.replaceChildren(...options);
  select.value = selected || '';
 }

 function validateModel(models, id, caps, label, allowUnconfigured = false) {
  if (allowUnconfigured && !id) return;
  if (!models.some(model => (!id || model.id === id) && modelCapabilities(caps).every(cap => model.capabilities.includes(cap)))) {
   throw Error(label + '没有匹配所选能力的可用模型。请选择其他模型，或到「提供者配置」启用模型。');
  }
 }

 function requiredText(scope, name, label) {
  const value = control(scope, name).value;
  if (!value.trim()) throw Error('请填写' + label);
  return value;
 }

 function capabilityValues(scope) {
  return Array.from(scope.querySelectorAll('[name=capability]:checked'), input => input.value);
 }

 function roleFields(scope, role, models, member = false) {
  scope.innerHTML = `
    <label>名称<input name="name" maxlength="100" required></label>
    <label>角色职责<textarea name="role" maxlength="2000" rows="2" required></textarea></label>
    <label>角色提示词 <small>选填</small><textarea name="prompt" rows="3" maxlength="12000"></textarea></label>
    <fieldset class="organization-capabilities"><legend>能力</legend></fieldset>
    <label>模型<select name="model_id"></select></label>
    ${member ? `<label>岗位标识<input name="key" maxlength="64" pattern="[a-zA-Z][a-zA-Z0-9_\\-]{0,63}" spellcheck="false" required>
      <small>字母开头，可含数字、下划线和连字符。工作流通过此标识选择岗位。</small></label>
    <label>执行指令<textarea name="instruction" rows="3" maxlength="6000" required></textarea></label>` : ''}
  `;
  for (const key of member ? ['name','role','prompt','key','instruction'] : ['name','role','prompt']) {
   control(scope, key).value = role[key] ?? '';
  }
  const group = scope.querySelector('.organization-capabilities');
  const caps = member ? [...teamCapabilities] : [...new Set([
   ...Object.keys(capabilityLabels), ...models.flatMap(model => model.capabilities)
  ])];
  caps.push(...role.capabilities.filter(capability => capability.startsWith('tool:') && !caps.includes(capability)));
  for (const capability of caps) {
   const label = document.createElement('label');
   const input = document.createElement('input');
   input.type = 'checkbox';
   input.name = 'capability';
   input.value = capability;
   input.checked = role.capabilities.includes(capability);
   input.disabled = member && capability === 'text';
   label.append(input, Object.hasOwn(capabilityLabels, capability) ? capabilityLabels[capability] : capability);
   group.append(label);
  }
  const model = control(scope, 'model_id');
  const fallback = member ? '继承组织模型' : '自动选用匹配能力的模型';
  modelOptions(model, models, capabilityValues(scope), fallback, role.model_id);
  group.addEventListener('change', () => modelOptions(model, models, capabilityValues(scope), fallback, model.value));
 }

 function readRole(scope, models, team = null) {
  const name = requiredText(scope, 'name', team ? '岗位名称' : '角色名称');
  const role = requiredText(scope, 'role', '角色职责');
  const capabilities = capabilityValues(scope);
  if (capabilities.length < 1 || capabilities.length > 12) throw Error('请选择 1 到 12 项模型能力与工具授权');
  const model_id = control(scope, 'model_id').value || null;
  validateModel(models, model_id ?? team?.model, capabilities, '「' + name + '」');
  const value = {name, role, capabilities, model_id, prompt:control(scope, 'prompt').value};
  if (team) {
   const key = control(scope, 'key').value;
   if (team.keys.has(key)) throw Error('岗位标识重复：' + key);
   team.keys.add(key);
   value.key = key;
   value.instruction = requiredText(scope, 'instruction', '岗位「' + name + '」的执行指令');
  }
  return value;
 }

 function unitFields(unit, {units, models}) {
  fields.innerHTML = `
   <div class="organization-grid">
    <label>组织名称<input name="name" maxlength="100" required></label>
    <label>上级组织<select name="parent_id"></select></label>
   </div>
   <label>组织职责<textarea name="purpose" rows="3" maxlength="3000" required></textarea></label>
   <label>组织模型<select name="model_id"></select></label>
   <label>组织提示词 <small>选填</small><textarea name="prompt" rows="4" maxlength="12000"></textarea></label>
   <section class="organization-team" aria-labelledby="crew-heading">
    <div class="row"><h3 id="crew-heading">Crew 团队</h3><label class="inline-check"><input type="checkbox" name="crew_enabled">启用团队</label></div>
    <p class="muted">岗位按列表顺序执行。更改会应用于后续工作项，已受理的执行保留原名单。</p>
    <fieldset class="organization-team-fields" data-team>
     <legend class="organization-sr-only">团队岗位</legend>
     <div data-members></div>
     <button type="button" data-add-member>添加岗位</button>
     <small data-limit hidden>已达到 6 个岗位上限</small>
    </fieldset>
   </section>
  `;
  for (const name of ['name','purpose','prompt']) control(fields, name).value = unit[name] ?? '';
  const parent = control(fields, 'parent_id');
  parent.add(new Option('无上级组织', ''));
  for (const choice of parentChoices(units, unit.id)) {
   parent.add(new Option(choice.name + ' · ' + (choice.alias || choice.id), choice.id));
  }
  parent.value = unit.parent_id ?? '';
  const model = control(fields, 'model_id'), required = ['text', 'json'];
  modelOptions(model, models, required, '自动选用文本与结构化输出模型', unit.model_id);

  const team = fields.querySelector('[data-team]');
  const members = team.querySelector('[data-members]');
  const add = team.querySelector('[data-add-member]');
  const enabled = control(fields, 'crew_enabled');

  function refreshOrder() {
   const count = members.children.length;
   Array.from(members.children).forEach((panel, index) => {
    panel.querySelector('legend').textContent = '岗位 ' + (index + 1);
    panel.querySelector('[data-up]').disabled = index === 0;
    panel.querySelector('[data-down]').disabled = index === count - 1;
    panel.querySelector('[data-remove]').disabled = count === 1;
   });
   add.disabled = count >= 6;
   team.querySelector('[data-limit]').hidden = count < 6;
  }

  function addMember(value = {capabilities: ['text','json']}, focus = false) {
   const panel = document.createElement('fieldset');
   panel.className = 'organization-member';
   panel.innerHTML = `<legend></legend><div data-role-fields></div>
     <div class="actions">
      <button type="button" data-up>上移</button>
      <button type="button" data-down>下移</button>
      <button type="button" data-remove>移除岗位</button>
     </div>`;
   roleFields(panel.querySelector('[data-role-fields]'), value, models, true);
   members.append(panel);
   const up = panel.querySelector('[data-up]'), down = panel.querySelector('[data-down]');
   up.onclick = () => {
    members.insertBefore(panel, panel.previousElementSibling);
    refreshOrder();
    (up.disabled ? down : up).focus();
   };
   down.onclick = () => {
    members.insertBefore(panel.nextElementSibling, panel);
    refreshOrder();
    (down.disabled ? up : down).focus();
   };
   panel.querySelector('[data-remove]').onclick = () => {
    const next = panel.nextElementSibling || panel.previousElementSibling;
    panel.remove();
    refreshOrder();
    control(next, 'name').focus();
   };
   refreshOrder();
   if (focus) control(panel, 'name').focus();
  }
  for (const member of unit.crew?.members ?? []) addMember(member);
  enabled.checked = Boolean(unit.crew);
  team.hidden = team.disabled = !enabled.checked;
  enabled.onchange = () => {
   team.hidden = team.disabled = !enabled.checked;
   if (enabled.checked && !members.children.length) addMember();
  };
  add.onclick = () => addMember(undefined, true);

  return () => {
   const name = requiredText(fields, 'name', '组织名称');
   const purpose = requiredText(fields, 'purpose', '组织职责');
   const model_id = model.value;
   validateModel(models, model_id, required, '组织', true);
   const context = {model:model_id, keys:new Set()};
   const crew = enabled.checked
    ? {process:'sequential', members:Array.from(members.children, member => readRole(member, models, context))}
    : null;
   return {name, purpose, model_id, parent_id:parent.value || null, prompt:control(fields, 'prompt').value, crew};
  };
 }

 function open(title, read, commit, ui) {
  const workspace = ui.workspace();
  dialog.querySelector('h2').textContent = title;
  error.hidden = true;
  error.textContent = '';
  form.onsubmit = async event => {
   event.preventDefault();
   if (save.disabled) return;
   error.hidden = true;
   try {
    if (workspace !== ui.workspace()) throw Error('当前空间已切换，请切回原空间后保存');
    const value = read();
    fields.disabled = save.disabled = true;
    dialog.setAttribute('aria-busy', 'true');
    save.textContent = '保存中…';
    await commit(value);
    dialog.close();
    ui.tell('已保存');
    await ui.render();
   } catch (failure) {
    error.hidden = false;
    error.textContent = failure.message;
    error.focus();
   } finally {
    fields.disabled = save.disabled = false;
    dialog.removeAttribute('aria-busy');
    save.textContent = '保存';
   }
  };
  control(fields, 'name').setAttribute('autofocus', '');
  dialog.querySelector('.organization-dialog-body').scrollTop = 0;
  dialog.showModal();
 }

 for (const button of dialog.querySelectorAll('[data-close], [data-cancel]')) {
  button.onclick = () => { if (!save.disabled) dialog.close(); };
 }
 dialog.oncancel = event => { if (save.disabled) event.preventDefault(); };

 function show(content, state, ui) {
  const {api, heading, empty, esc} = ui;
  const {units, agents, models} = state;
  const byId = new Map(units.map(unit => [unit.id, unit]));
  content.innerHTML = heading('组织与角色', '配置组织职责、团队岗位和常驻角色。', '<button id="new-unit" class="primary">创建组织</button>')
   + (units.length ? units.map(unit => {
    const parent = byId.get(unit.parent_id);
    const roles = agents.filter(agent => agent.unit_id === unit.id && !agent.project_id);
    return `<section class="panel organization-card">
     <div class="row"><h2>${esc(unit.name)}</h2><small>v${esc(unit.version)}</small></div>
     <p class="muted">${parent ? '上级 · ' + esc(parent.name) : '无上级组织'} · ${esc(unit.alias || unit.id)}</p>
     <p>${esc(unit.purpose)}</p>
     ${unit.crew ? `<h3>Crew 团队</h3><ol>${unit.crew.members.map(member => `<li><strong>${esc(member.name)}</strong> · ${esc(member.role)} <small>${esc(member.key)}</small></li>`).join('')}</ol>` : ''}
     ${roles.length ? `<h3>常驻角色</h3><ul>${roles.map(role => `<li><strong>${esc(role.name)}</strong> · ${esc(role.role)} <small>${esc(role.model_id)}</small></li>`).join('')}</ul>` : ''}
     <div class="actions"><button data-edit-unit="${esc(unit.id)}">编辑组织</button><button data-add-role="${esc(unit.id)}">添加常驻角色</button></div>
    </section>`;
   }).join('') : empty('可以从空白创建组织，也可以先安装一个组织包。'));

  const openUnit = unit => {
   const path = unit.id ? 'organizations/' + encodeURIComponent(unit.id) : 'organizations';
   open(unit.id ? '编辑组织' : '创建组织', unitFields(unit, state),
    value => api(path, {method:unit.id ? 'PUT' : 'POST', value}), ui);
  };
  content.querySelector('#new-unit').onclick = () => openUnit({});
  for (const button of content.querySelectorAll('[data-edit-unit]')) {
   button.onclick = () => openUnit(byId.get(button.dataset.editUnit));
  }
  for (const button of content.querySelectorAll('[data-add-role]')) {
   button.onclick = () => {
    const unit = byId.get(button.dataset.addRole);
    roleFields(fields, {capabilities:['text','json']}, models);
    open('添加常驻角色 · ' + unit.name, () => readRole(fields, models),
     value => api('organizations/' + encodeURIComponent(unit.id) + '/roles', {method:'POST', value}), ui);
   };
  }
 }

 return {show};
})();
