import React, {createContext, memo, useContext, useEffect, useId, useMemo, useRef, useState, useSyncExternalStore} from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';
import {
  applyNodeChanges, Background, BaseEdge, getBezierPath, getViewportForBounds,
  Handle, MarkerType, Position, ReactFlow, SelectionMode,
} from '@xyflow/react';
import {Boxes, Braces, Check, Flag, Group, Minus, Plus, Scan, SquareDashedMousePointer} from 'lucide-react';
import {Button} from './components/ui/button';
import '@xyflow/react/dist/style.css';

const canvasActions = createContext(null);
const minimumZoom = 0.025;
const maximumZoom = 2.5;

function eventPoint(event) {
  const position = event.changedTouches?.[0] || event.touches?.[0] || event;
  return {x: position.clientX, y: position.clientY};
}

const WorkflowNode = memo(function WorkflowNode({id, data, selected}) {
  const graph = useContext(canvasActions);
  const {node, source} = data;
  const {actions} = graph;
  const incoming = Boolean(actions.connect && node.input !== false);
  const outgoing = Boolean((actions.connect || actions.add) && node.output !== false);
  const canBranch = Boolean(actions.add && node.add !== false);
  const Icon = node.type === 'tool' ? Braces : node.type === 'group' ? Group :
    node.type === 'result' ? Flag : Boxes;

  function port(type, active) {
    const input = type === 'target';
    const activate = event => {
      event.stopPropagation();
      if (graph.ignoreClick && event.detail) return;
      if (input) graph.activate(id, event);
      else graph.arm(id);
    };
    return <Handle type={type} position={input ? Position.Left : Position.Right}
      className={'flow-port ' + (input ? 'flow-in' : 'flow-out') + (active ? '' : ' flow-port-passive')}
      isConnectable={active} isConnectableStart={active && !input} isConnectableEnd={active && input}
      data-port={input ? 'in' : 'out'} role={active ? 'button' : undefined}
      aria-hidden={!active} tabIndex={active ? 0 : -1}
      aria-label={active ? (input ? '连接到 ' : '从此环节连线 · ') + node.title : undefined}
      onClick={active ? activate : undefined}
      onKeyDown={active ? event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); activate(event); }
      } : undefined}/>;
  }

  return <article className={'flow-node' + (selected ? ' is-selected' : '') + (source ? ' is-source' : '')}
    data-node={id} data-state={node.state} data-kind={node.type}>
    {port('target', incoming)}
    <button type="button" className="flow-node-body nokey" aria-pressed={selected}
      onClick={event => {event.stopPropagation(); if (!graph.ignoreClick || !event.detail) graph.activate(id, event);}}>
      <span className="flow-node-heading"><span className="flow-node-type"><Icon aria-hidden="true"/>{node.kind || '步骤'}</span>
        {selected && <span className="flow-node-picked"><Check aria-hidden="true"/>已选</span>}</span>
      <strong title={node.title}>{node.title}</strong>
      <small>{node.meta || id}</small>
    </button>
    {port('source', outgoing)}
    {canBranch && <button type="button" className="flow-plus nodrag nopan nokey" data-port="add"
      aria-label={'从 ' + node.title + ' 添加分支'} title="添加分支"
      onClick={event => {event.stopPropagation(); graph.branch(id);}}><Plus aria-hidden="true"/></button>}
  </article>;
});

function WorkflowEdge({id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd, style}) {
  let [path] = getBezierPath({sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition});
  if (data.rail !== undefined) {
    const turn = Math.min(32, (targetX - sourceX) / 4);
    path = `M ${sourceX} ${sourceY} C ${sourceX + turn} ${sourceY} ${sourceX} ${data.rail} ${sourceX + turn} ${data.rail} H ${targetX - turn} C ${targetX} ${data.rail} ${targetX - turn} ${targetY} ${targetX} ${targetY}`;
  }
  return <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style}/>;
}
const nodeTypes = {stage: WorkflowNode};
const edgeTypes = {dependency: WorkflowEdge};

function dependencyEdges(nodes, links, size) {
  const byId = new Map(nodes.map(node => [node.id, node]));
  return links.flatMap(({from, to, group}, index) => {
    const a = byId.get(from), b = byId.get(to);
    if (!a || !b) return [];
    const selected = a.selected || b.selected;
    const color = selected ? 'var(--op-edge-selected)' : 'var(--op-edge)';
    const ax = a.position.x + size.width, bx = b.position.x;
    const ay = a.position.y + size.height / 2, by = b.position.y + size.height / 2;
    // Direct dependencies may skip columns. Detour above any intermediate cards.
    const blockers = nodes.filter(other => other !== a && other !== b
      && other.position.x < bx && other.position.x + size.width > ax
      && other.position.y < Math.max(ay, by) + 10 && other.position.y + size.height > Math.min(ay, by) - 10);
    const rail = blockers.length ? Math.min(ay, by, ...blockers.map(node => node.position.y)) - 22 - index % 4 * 10 : undefined;
    return [{id: String(index), source: from, target: to, type: 'dependency', selectable: false,
      focusable: false, data: {rail}, markerEnd: {type: MarkerType.ArrowClosed, color, width: 16, height: 16},
      style: {stroke: color, strokeWidth: selected ? 2.6 : 1.7, strokeDasharray: group ? '5 5' : undefined}}];
  });
}

function Canvas({graph}) {
  const model = useSyncExternalStore(graph.subscribe, graph.read);
  const {nodes, links, multiple, feedback, fitRequest} = model;
  const {actions, size} = graph;
  const surface = useRef(null);
  const boxInitial = useRef(null);
  const connectionDrag = useRef(false);
  const [instance, setInstance] = useState(null);
  const [zoom, setZoom] = useState(graph.state.view?.zoom || 1);
  const instanceId = useId().replace(/:/g, '');
  const edges = useMemo(() => dependencyEdges(nodes, links, size), [nodes, links, size]);
  const fit = (opening = false) => {
    if (!instance || !surface.current?.clientWidth || !surface.current.clientHeight) return;
    if (!nodes.length) {instance.setViewport({x: 24, y: 24, zoom: 1}); return;}
    const left = Math.min(...nodes.map(node => node.position.x));
    const top = Math.min(...nodes.map(node => node.position.y));
    const width = Math.max(...nodes.map(node => node.position.x + size.width)) - left;
    const height = Math.max(...nodes.map(node => node.position.y + size.height)) - top;
    const available = surface.current;
    const overview = getViewportForBounds({x: left, y: top, width, height},
      available.clientWidth, available.clientHeight, minimumZoom, 1, 0.13);
    // On a phone, open on the inspected task at reading scale. Fit always shows the whole diagram.
    if (opening && available.clientWidth < 600 && overview.zoom < 0.9) {
      const focus = nodes.find(node => node.id === graph.selected.at(-1)) || nodes[0];
      instance.setViewport({x: available.clientWidth / 2 - (focus.position.x + size.width / 2),
        y: available.clientHeight / 2 - (focus.position.y + size.height / 2), zoom: 1});
    } else instance.setViewport(overview);
  };
  const lastFit = useRef(0);
  useEffect(() => {
    if (instance && fitRequest !== lastFit.current && surface.current?.clientWidth) {
      lastFit.current = fitRequest;
      fit(true);
    }
  }, [instance, fitRequest]);
  const gesture = name => {
    if (!surface.current) return;
    if (name) surface.current.dataset.gesture = name;
    else delete surface.current.dataset.gesture;
  };
  function endConnection(event, finished) {
    gesture(null);
    const cancelled = connectionDrag.current === 'cancelled';
    if (!connectionDrag.current) return;
    connectionDrag.current = false;
    graph.ignoreClick = true;
    if (cancelled || event.type.endsWith('cancel') || finished.isValid || !finished.fromNode) return;
    const point = eventPoint(event);
    const hit = document.elementFromPoint(point.x, point.y);
    if (!hit || !surface.current?.contains(hit)) return;
    const target = hit.closest('[data-node]')?.dataset.node;
    if (target) graph.connect(finished.fromNode.id, target);
    else if (hit.closest('.react-flow__pane')) graph.branch(finished.fromNode.id, instance.screenToFlowPosition(point));
  }
  function keys(event) {
    if (event.key === 'Escape') {
      if (connectionDrag.current) connectionDrag.current = 'cancelled';
      boxInitial.current = null; gesture(null); graph.cancel(); return;
    }
    if (event.target !== surface.current || !instance) return;
    const delta = {ArrowLeft: [45, 0], ArrowRight: [-45, 0], ArrowUp: [0, 45], ArrowDown: [0, -45]}[event.key];
    if (delta) {
      const view = instance.getViewport();
      instance.setViewport({...view, x: view.x + delta[0], y: view.y + delta[1]});
    } else if (event.key === '=' || event.key === '+') instance.zoomIn();
    else if (event.key === '-') instance.zoomOut();
    else if (event.key === '0') fit();
    else return;
    event.preventDefault();
  }
  return <canvasActions.Provider value={graph}>
    <div className="flow-tools" role="toolbar" aria-label="画布操作">
      <Button size="sm" variant="ghost" data-fit="" onClick={() => fit()} title="适应画布"><Scan/>适应</Button>
      <span className="flow-tools-separator" aria-hidden="true"/>
      <Button size="icon-sm" variant="ghost" data-zoom="-1" aria-label="缩小" onClick={() => instance?.zoomOut()}><Minus/></Button>
      <output aria-label="缩放比例">{Math.round(zoom * 100)}%</output>
      <Button size="icon-sm" variant="ghost" data-zoom="1" aria-label="放大" onClick={() => instance?.zoomIn()}><Plus/></Button>
      {actions.selectMany && <Button size="sm" variant="outline" data-multiselect="" aria-pressed={multiple}
        onClick={() => graph.toggleMultiple()} title="点选或框选节点"><SquareDashedMousePointer/>多选</Button>}
      {actions.packageSelection && <Button size="sm" variant="outline" data-package-run=""
        disabled={!actions.canPackage(graph.selected)} onClick={() => actions.packageSelection(graph.selected)}>封装为工作流</Button>}
      <span className="flow-hint">{actions.add ? '拖动右端口创建分支' : '拖动空白平移 · 滚轮缩放'}</span>
    </div>
    <div ref={surface} className={'flow-viewport' + (multiple ? ' is-multiselect' : '')} tabIndex={0}
      role="region" aria-label="工作流画布，方向键平移，正负键缩放" onKeyDown={keys}
      onPointerCancelCapture={() => {gesture(null); boxInitial.current = null;}}
      onPointerDownCapture={event => {
        graph.ignoreClick = false;
        if (event.target.classList.contains('react-flow__pane')) {
          boxInitial.current = {selection: graph.selected, origin: instance?.screenToFlowPosition(eventPoint(event))};
        }
      }}>
      <ReactFlow id={'orchpact-' + instanceId} nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
        onInit={setInstance} defaultViewport={graph.state.view || {x: 24, y: 24, zoom: 1}}
        onMove={(_, view) => {graph.state.view = view; setZoom(view.zoom);}}
        onMoveStart={event => {if (event) gesture('pan');}} onMoveEnd={() => gesture(null)}
        minZoom={minimumZoom} maxZoom={maximumZoom} zoomOnDoubleClick={false} deleteKeyCode={null}
        nodesDraggable={Boolean(actions.move)} nodesFocusable={false} edgesFocusable={false}
        nodesConnectable={Boolean(actions.connect || actions.add)} connectOnClick={false}
        connectionDragThreshold={5} connectionRadius={12} onConnectStart={() => {connectionDrag.current = true; gesture('wire'); surface.current?.focus({preventScroll: true});}}
        onConnect={({source, target}) => {if (connectionDrag.current !== 'cancelled') graph.connect(source, target);}} onConnectEnd={endConnection}
        connectionLineStyle={{stroke: 'var(--op-edge-selected)', strokeWidth: 2.5, strokeDasharray: '7 4'}}
        selectionKeyCode={actions.selectMany ? 'Shift' : null} selectionMode={SelectionMode.Partial}
        panOnDrag={!multiple} selectionOnDrag={multiple} selectNodesOnDrag={false} multiSelectionKeyCode={null}
        onSelectionStart={() => gesture('box')}
        onSelectionEnd={event => {
          gesture(null);
          const origin = boxInitial.current?.origin;
          if (!origin) return;
          const end = instance.screenToFlowPosition(eventPoint(event));
          const area = {x: Math.min(origin.x, end.x), y: Math.min(origin.y, end.y),
            width: Math.abs(end.x - origin.x), height: Math.abs(end.y - origin.y)};
          const hits = instance.getIntersectingNodes(area, true).map(node => node.id);
          const baseline = event.shiftKey ? boxInitial.current.selection : [];
          graph.pick([...new Set([...baseline, ...hits])], true);
          boxInitial.current = null;
        }}
        onNodesChange={graph.onNodesChange}
        onNodeDragStart={() => gesture('move')}
        onNodeDragStop={(_, node, moved) => {
          gesture(null);
          for (const stage of moved?.length ? moved : [node]) actions.move?.(stage.id, stage.position);
        }}
        onPaneClick={event => {
          if (graph.ignoreClick) return;
          if (graph.armed) graph.branch(graph.armed, instance.screenToFlowPosition(eventPoint(event)));
          else if (actions.selectMany && !multiple && !event.shiftKey) graph.pick([], true);
        }}>
        <Background id={'grid-' + instanceId} gap={22} size={1.2} color="var(--op-line)"/>
      </ReactFlow>
      {!nodes.length && actions.onEmpty && <div className="flow-empty"><Button data-flow-first="" variant="outline"
        onClick={actions.onEmpty}><Plus aria-hidden="true"/>添加第一个环节</Button></div>}
    </div>
    <p className="flow-feedback" role="status">{feedback}</p>
  </canvasActions.Provider>;
}

function mount(host, actions, size) {
  host.classList.add('flow-canvas');
  const state = actions.state || {};
  const root = createRoot(host);
  const listeners = new Set();
  let model = {nodes: [], links: [], fitRequest: 0, multiple: false, feedback: ''};
  let selected = [], armed = null;
  function update(changes) {
    model = {...model, ...changes};
    for (const listener of listeners) listener();
  }
  function mark() {
    return model.nodes.map(node => {
      const source = node.id === armed, active = selected.includes(node.id);
      return node.data.source === source && node.selected === active ? node :
        {...node, selected: active, data: {...node.data, source}};
    });
  }
  function pick(ids, many = false) {
    selected = [...ids];
    update({nodes: mark()});
    if (many) actions.selectMany?.(selected);
    else if (ids.length) actions.select?.(ids[0]);
  }
  function arm(id) {
    armed = id === armed ? null : id;
    update({nodes: mark(), feedback: armed ? [actions.connect && '选择目标节点连线',
      actions.add && '点击空白添加分支'].filter(Boolean).join('，') : ''});
  }
  function connect(from, to) {
    armed = null;
    let feedback;
    if (!actions.connect || model.nodes.find(n => n.id === to)?.data.node.input === false) feedback = '此节点暂时不能接入新的上游';
    else {
      try {feedback = actions.connect(from, to) || '依赖已连接';}
      catch (error) {feedback = error.message;}
    }
    update({nodes: mark(), feedback});
  }
  function branch(from, point) {
    armed = null;
    const allowed = actions.add && model.nodes.find(n => n.id === from)?.data.node.add !== false;
    update({nodes: mark(), feedback: allowed ? '' : '此节点暂时不能添加分支'});
    if (allowed) actions.add(from, point);
  }
  function activate(id, event) {
    if (armed) {connect(armed, id); return;}
    if (actions.selectMany && (model.multiple || event.shiftKey || event.ctrlKey || event.metaKey)) {
      pick(selected.includes(id) ? selected.filter(key => key !== id) : [...selected, id], true);
    } else pick([id]);
  }
  const graph = {actions, size, state, read: () => model, subscribe(listener) {
    listeners.add(listener); return () => listeners.delete(listener);
  }, get selected() {return selected;}, get armed() {return armed;}, pick, arm, connect, branch, activate,
    toggleMultiple() {
      const multiple = !model.multiple;
      update({multiple, feedback: multiple ? '点选多个节点，或在空白处拖动框选。' : ''});
      if (multiple && actions.clearOnMultiple) pick([], true);
    },
    cancel() {
      armed = null;
      update({feedback: '', multiple: false, nodes: mark()});
    },
    onNodesChange(changes) {
      const geometry = changes.filter(change => change.type === 'position' || change.type === 'dimensions');
      if (geometry.length) update({nodes: applyNodeChanges(geometry, model.nodes)});
    },
  };
  let mounted = false;
  actions.signal?.addEventListener('abort', () => root.unmount(), {once: true});
  return {
    setGraph(items, links, options, positions) {
      selected = options.selectedIds || (options.selected ? [options.selected] : []);
      if (!positions.has(armed)) armed = null;
      const nodes = items.map(node => ({id: node.id, type: 'stage', position: positions.get(node.id),
        width: size.width, height: size.height, selected: selected.includes(node.id),
        data: {node, source: node.id === armed}, sourcePosition: Position.Right, targetPosition: Position.Left}));
      update({nodes, links, fitRequest: model.fitRequest + Number(Boolean(options.fit || !state.view))});
      // Route refreshers restore keyboard focus as soon as their canvas has mounted.
      if (!mounted) {
        mounted = true;
        flushSync(() => root.render(<Canvas graph={graph}/>));
      }
    },
    positions: () => new Map(model.nodes.map(node => [node.id, {...node.position}])),
  };
}

globalThis.OrchpactFlow = {mount};
