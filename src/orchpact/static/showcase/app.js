'use strict';

// Illustrations use local synthetic records only. No platform or provider requests.
const byId = id => document.getElementById(id);
const initial = {
  label: '受理', title: '先把委托的边界写清楚。',
  description: '确认发起者、Workspace、输入契约和行动授权，再创建工作项。组织关系不会替代授权。',
  badge: '准入检查', nodes: ['active', 'idle', 'idle', 'idle'],
  file: '委托契约', version: 'INPUT',
  content: '目标：汇总记录并列明来源\n输入：A = 3，B = 5\n交付：总数 + 来源编号\n验收：计算与独立审校',
  note: '示意数据始终只保留在本页。'
};
const planning = {
  label: '计划', title: '把协作写成一张任务图。',
  description: '组织包定义计算与审校职责，并声明依赖。平台统一检查任务图、输入输出契约与授权边界。',
  badge: '计划已校验', nodes: ['done', 'active', 'idle', 'idle'],
  file: '计划版本', version: 'REVISION 1',
  content: '计算汇总 → 独立审校 → 文件交付\n\n计算产物：result.json\n审校产物：audit.json\n审校引用：当前计算产物',
  note: '固定协作与自主规划，使用相同的工作项和依赖。'
};
const computed = {
  label: '执行', title: '先交出文件，再交给审校。',
  description: '执行前检查能力和预算，完成后记录用量与产物。文件拥有自己的版本和哈希，可以被后续工作项引用。',
  badge: '产物已登记', nodes: ['done', 'done', 'active', 'idle'],
  file: 'result.json', version: '初稿',
  content: '{\n  "total": 8,\n  "source_ids": ["A", "B"]\n}',
  note: '产物登记后保持不可变，修改将生成新的版本。'
};
const reviewed = {
  label: '审校', title: '审的是这一版具体成果。',
  description: '独立工作项核对总数与来源，并明确记录它审查的产物 ID。只有当前产物通过当前验收，才满足完整交付条件。',
  badge: '当前版本通过', nodes: ['done', 'done', 'done', 'active'],
  file: 'audit.json', version: '初稿审校',
  content: '{\n  "passed": true,\n  "reviewed_artifact_ids": ["draft-1"],\n  "issues": []\n}',
  note: '专业审校可以调用模型；结构契约由确定性规则校验。'
};
const delivered = {
  label: '交付', title: '把成果、来源和验收一起交付。',
  description: '计算与独立审校均完成，交付总数 8 和来源 A、B。调用回执、用量、产物与验收记录各自保留，便于追溯。',
  badge: '完整交付', nodes: ['done', 'done', 'done', 'done'],
  file: '交付清单', version: 'COMPLETED',
  content: 'result.json   总数 8，来源 A / B\naudit.json    当前产物已通过审校\n\n附带：产物版本、哈希与调用记录',
  note: '示意流程完成；内容质量仍需用真实任务单独评估。'
};
const scenarios = {
  normal: [initial, planning, computed, reviewed, delivered],
  repair: [initial, planning,
    {...computed, title: '第一版有文件，也可能有缺口。',
      description: '计算工作项交出总数 9，并遗漏来源 B。产物可以通过结构校验，但内容仍需要独立审校。',
      content: '{\n  "total": 9,\n  "source_ids": ["A"]\n}',
      note: '结构正确与内容正确，需要分别检查。'},
    {...reviewed, label: '驳回', title: '保留第一版，指出具体缺口。',
      description: '独立审校发现总数错误和来源遗漏。平台记录不通过，并按预先声明的返工范围，定向修订计算工作项。',
      badge: '需要返工', warning: true, repair: true, nodes: ['done', 'rejected', 'rejected', 'idle'],
      content: '{\n  "passed": false,\n  "reviewed_artifact_ids": ["draft-1"],\n  "issues": ["总数应为 8", "缺少来源 B"]\n}',
      note: '返工有次数与范围边界，旧产物和旧审校继续保留。'},
    {...computed, label: '返工', title: '只修订有缺口的工作项。',
      description: '计算工作项根据审校意见生成修订稿。已有产物不被覆盖；修订稿需要自己的审校结果。',
      badge: '产物版本更新', version: '修订稿', repair: true,
      note: '初稿保留，修订稿等待新的独立审校。'},
    {...reviewed, label: '复核', title: '新的版本，重新独立验收。', version: '修订稿审校',
      content: '{\n  "passed": true,\n  "reviewed_artifact_ids": ["draft-2"],\n  "issues": []\n}',
      note: '审校明确绑定修订稿的产物 ID，陈旧审校不能放行新产物。'},
    {...delivered, note: '交付修订稿；初稿、返工理由和两轮审校均保留。'}
  ],
  unknown: [initial, planning,
    {...computed, label: '调用', title: '请求发出，结果还没有确认。',
      description: '外部调用已取得额度预留，实际回执尚未完整收到。此时不能推断调用失败，也不能把未知用量当作零。',
      badge: '等待回执', warning: true, nodes: ['done', 'active', 'idle', 'idle'],
      file: '调用记录', version: 'STARTED',
      content: '请求：已发出\n回执：尚未确认\n额度：保持预留\n付款人：原发起者',
      note: '等待状态不代表尚未计费。'},
    {...computed, label: '核定', title: '先核对事实，再决定下一步。',
      description: '中断或重启后先恢复真实回执；未知结果继续待核定，不自动重放。确认用量后，仍按原付款人和幂等边界结算。',
      badge: '待核定', warning: true, nodes: ['done', 'waiting', 'idle', 'idle'],
      file: '恢复记录', version: 'UNKNOWN',
      content: '结果：未知\n额度：保持预留\n自动重发：否\n下一步：核对真实回执',
      note: '演示停在待核定状态，不将未知结果显示为成功交付。'}
  ]
};

let scenario = 'normal';
let plan = 'fixed';
let step = 0;
let playing = false;
let playbackTimer;
const copyTimers = new Map();
const statusLabels = {idle: '待执行', active: '进行中', done: '已完成', rejected: '待返工', waiting: '待核定'};
const nodeIds = ['admit', 'work', 'review', 'deliver'];

function selected(buttons, current) {
  buttons.forEach(button => button.setAttribute('aria-pressed', String(button === current)));
}
function stopPlayback() {
  clearTimeout(playbackTimer);
  playbackTimer = undefined;
  playing = false;
}
function render() {
  const steps = scenarios[scenario];
  let current = steps[step];
  if (step === 1 && plan === 'autonomous') {
    current = {...current, title: '规划提案，也要经过共同的校验。',
      description: '模型根据目标提出任务、职责与依赖。提案通过结构、契约和授权校验后，才会进入同一套执行机制。',
      content: '模型提出协作计划\n  ↓ 结构 / 依赖 / 契约 / 授权校验\n计算汇总 → 独立审校 → 文件交付\n\n后续变更需要理由与版本检查。'};
  }
  byId('step-number').textContent = `STEP ${String(step + 1).padStart(2, '0')} / ${String(steps.length).padStart(2, '0')}`;
  byId('step-state').textContent = current.badge;
  byId('step-state').dataset.tone = current.warning ? 'warning' : 'normal';
  byId('step-title').textContent = current.title;
  byId('step-description').textContent = current.description;
  byId('artifact-name').textContent = current.file;
  byId('artifact-version').textContent = current.version;
  byId('artifact-content').textContent = current.content;
  byId('artifact-note').textContent = current.note;
  byId('graph-repair').hidden = !current.repair;
  nodeIds.forEach((name, index) => {
    const node = document.querySelector(`[data-node="${name}"]`);
    node.dataset.status = current.nodes[index];
    node.querySelector('.node-status').textContent = statusLabels[current.nodes[index]];
  });
  byId('play-symbol').textContent = playing ? 'Ⅱ' : '▶';
  byId('play-label').textContent = playing ? '暂停示意' : step === steps.length - 1 ? '重播示意' : step ? '继续播放' : '播放示意';
  byId('next-button').textContent = step === steps.length - 1 ? '从头查看 ↺' : '下一步 →';
  [...byId('step-timeline').children].forEach((button, index) => {
    button.setAttribute('aria-pressed', String(step === index));
    if (step === index) button.setAttribute('aria-current', 'step');
    else button.removeAttribute('aria-current');
  });
}
function buildTimeline() {
  const buttons = scenarios[scenario].map((item, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.setAttribute('aria-label', `第 ${index + 1} 步：${item.label}`);
    const number = document.createElement('span');
    number.textContent = String(index + 1).padStart(2, '0');
    button.append(number, document.createTextNode(item.label));
    button.addEventListener('click', () => {
      stopPlayback();
      step = index;
      render();
    });
    return button;
  });
  byId('step-timeline').replaceChildren(...buttons);
}
function scheduleNext() {
  clearTimeout(playbackTimer);
  playbackTimer = setTimeout(() => {
    if (!playing) return;
    if (step < scenarios[scenario].length - 1) step++;
    if (step === scenarios[scenario].length - 1) stopPlayback();
    render();
    if (playing) scheduleNext();
  }, 4200);
}
const scenarioButtons = [...document.querySelectorAll('[data-scenario]')];
scenarioButtons.forEach(button => button.addEventListener('click', () => {
  stopPlayback();
  scenario = button.dataset.scenario;
  step = 0;
  selected(scenarioButtons, button);
  buildTimeline();
  render();
}));
const planButtons = [...document.querySelectorAll('[data-plan]')];
planButtons.forEach(button => button.addEventListener('click', () => {
  stopPlayback();
  plan = button.dataset.plan;
  selected(planButtons, button);
  byId('plan-description').textContent = plan === 'fixed'
    ? '在组织包里预先声明计算与审校职责，按明确依赖推进。'
    : '由模型提出职责、任务与依赖，经共同校验后进入同一张图。';
  render();
}));
byId('next-button').addEventListener('click', () => {
  stopPlayback();
  step = (step + 1) % scenarios[scenario].length;
  render();
});
byId('play-button').addEventListener('click', () => {
  if (playing) stopPlayback();
  else {
    if (step === scenarios[scenario].length - 1) step = 0;
    playing = true;
    scheduleNext();
  }
  render();
});
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { stopPlayback(); render(); }
});
if ('IntersectionObserver' in window) {
  const observer = new IntersectionObserver(entries => {
    if (!entries[0].isIntersecting && playing) { stopPlayback(); render(); }
  });
  observer.observe(document.querySelector('.execution-lab'));
}

const modules = {
  kernel: {
    label: '01 / DOMAIN', title: '让执行有共同的规则。',
    description: '核心领域保留职责、授权、任务与验收契约。预定义计划和自主规划，都遵守相同的结构与边界。',
    points: ['三种关系分别建模', '计划变更有版本与理由', '当前产物对应当前验收'], tag: '纯领域结构与校验'
  },
  services: {
    label: '02 / ORCHESTRATION', title: '让一张任务图持续推进。',
    description: '组织命令与唯一调度器维护任务依赖、执行尝试、验收与定向返工。中断后的推进以真实回执和已有成果为依据。',
    points: ['按项目公平领取可运行任务', '每次行动前检查授权与预算', '保留部分成果和明确的未完成缺口'], tag: '单个本地调度器'
  },
  adapters: {
    label: '03 / INFRASTRUCTURE', title: '把状态与真实结果保存下来。',
    description: 'PostgreSQL 保存状态、账务和审计；本地文件保存产物与回执。CrewAI 执行团队协作与岗位行动循环，模型与工具经平台适配器调用。',
    points: ['业务与账务在同一事务中提交', '数据库与文件目录成对绑定', '保留价格快照、用量与真实回执'], tag: 'PostgreSQL + 本地文件'
  },
  interfaces: {
    label: '04 / ENTRY POINTS', title: '在入口确认身份和范围。',
    description: 'HTTP 与共享客户端把不同使用方的委托转换为显式执行上下文。个人会话、管理身份和应用签名各有自己的边界。',
    points: ['统一应用接入协议', '服务端检查 Workspace 与应用隔离', '应用私钥留在服务端网关'], tag: '身份与 Workspace 边界'
  }
};
const moduleButtons = [...document.querySelectorAll('[data-module]')];
moduleButtons.forEach(button => button.addEventListener('click', () => {
  const current = modules[button.dataset.module];
  selected(moduleButtons, button);
  byId('module-number').textContent = current.label;
  byId('module-title').textContent = current.title;
  byId('module-description').textContent = current.description;
  byId('module-tag').textContent = current.tag;
  byId('module-points').replaceChildren(...current.points.map(text => {
    const item = document.createElement('li');
    item.textContent = text;
    return item;
  }));
}));
function enableCopy(buttonId, sourceId, statusId, success, original) {
  byId(buttonId).addEventListener('click', async () => {
    clearTimeout(copyTimers.get(buttonId));
    try {
      await navigator.clipboard.writeText(byId(sourceId).textContent);
      byId(buttonId).textContent = '已复制 ✓';
      byId(statusId).textContent = success;
    } catch {
      const range = document.createRange();
      range.selectNodeContents(byId(sourceId));
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      byId(statusId).textContent = '请复制已选中的命令。';
    }
    copyTimers.set(buttonId, setTimeout(() => { byId(buttonId).textContent = original; copyTimers.delete(buttonId); }, 2500));
  });
}
enableCopy('copy-command', 'start-command', 'copy-status', '命令已复制。配置数据库连接后，再检查并启动实例。', '复制 ↗');
enableCopy('copy-contract', 'contract-command', 'contract-copy-status', '验证命令已复制。请在源码仓库根目录运行，并使用不存在的报告路径。', '复制验证命令 ↗');
window.addEventListener('pagehide', () => { stopPlayback(); copyTimers.forEach(timer => clearTimeout(timer)); copyTimers.clear(); });
buildTimeline();
render();
