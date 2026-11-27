const CARD_WIDTH=216, CARD_HEIGHT=94;
const COLUMN=284, ROW=125;

export function arrangeFlow(nodes,edges){
  const levels=new Map(), incoming=new Map(nodes.map(node=>[node.id,new Set()]));
  for(const edge of edges)incoming.get(edge.to)?.add(edge.from);
  const columns=[];
  const pending=new Map(nodes.map(node=>[node.id,node]));
  while(pending.size){
    const ready=[...pending.values()].filter(node=>[...incoming.get(node.id)].every(id=>!pending.has(id)));
    // Drafts and historical graphs can temporarily be cyclic. Keep every card reachable.
    const batch=ready.length?ready:[[...pending.values()][0]];
    for(const node of batch){
      const depth=Math.max(0,...[...incoming.get(node.id)].map(id=>(levels.get(id)??-1)+1));
      levels.set(node.id,depth);
      (columns[depth]??=[]).push(node.id);
      pending.delete(node.id);
    }
  }
  const height=Math.max(1,...columns.map(column=>column.length));
  return new Map(columns.flatMap((column,depth)=>column.map((id,index)=>
    [id,{x:depth*COLUMN,y:(index+(height-column.length)/2)*ROW}])));
}

export function introducesCycle(edges,from,to){
  if(from===to)return true;
  const outgoing=new Map();
  for(const edge of edges){
    if(!outgoing.has(edge.from))outgoing.set(edge.from,[]);
    outgoing.get(edge.from).push(edge.to);
  }
  const pending=[to],seen=new Set();
  while(pending.length){
    const node=pending.pop();
    if(node===from)return true;
    if(seen.has(node))continue;
    seen.add(node);
    pending.push(...(outgoing.get(node)||[]));
  }
  return false;
}

export function freeFlowPosition(origin,occupied){
  const clear=point=>!occupied.some(other=>Math.abs(other.x-point.x)<CARD_WIDTH+24&&Math.abs(other.y-point.y)<CARD_HEIGHT+18);
  if(clear(origin))return origin;
  for(let step=1;;step++){
    for(const shift of [step,-step]){
      const point={x:origin.x,y:origin.y+shift*ROW};
      if(clear(point))return point;
    }
  }
}

export function positionFlow(nodes,edges,saved={}){
  const automatic=arrangeFlow(nodes,edges),placed=new Map();
  for(const {id} of nodes){
    const point=Object.hasOwn(saved,id)?saved[id]:null;
    if(Number.isFinite(point?.x)&&Number.isFinite(point?.y))placed.set(id,point);
  }
  if(!placed.size)return automatic;
  for(const [id,origin] of automatic){
    if(!placed.has(id))placed.set(id,freeFlowPosition(origin,[...placed.values()]));
  }
  return placed;
}

export function selectionInBox(positions, start, end){
  const left=Math.min(start.x,end.x),right=Math.max(start.x,end.x);
  const top=Math.min(start.y,end.y),bottom=Math.max(start.y,end.y);
  return [...positions].filter(([,point])=>point.x<=right&&point.x+CARD_WIDTH>=left
    &&point.y<=bottom&&point.y+CARD_HEIGHT>=top).map(([id])=>id);
}

export function mountFlow(host,actions){
  const canvas=globalThis.OrchpactFlow.mount(host,actions,{width:CARD_WIDTH,height:CARD_HEIGHT});
  return {
    setGraph(nodes,edges,options={}){
      canvas.setGraph(nodes,edges,options,positionFlow(nodes,edges,options.positions));
    },
    positions:canvas.positions,
  };
}
