const test = require('node:test');
const assert = require('node:assert/strict');
const routes = import('../src/orchpact/static/kernel/studio-route.js');

test('opening a drawer on a run preserves its canvas, focus and pagination across reload',async()=>{
  const {resolveStudio} = await routes;
  const run = 'projects/run-1?focus=review%3Aitem';
  const filtered = resolveStudio('attention?kind=review&cursor=eyJ5',run);
  assert.equal(filtered.documentHash,run);
  assert.equal(filtered.drawer.params.get('cursor'),'eyJ5');
  const restored = resolveStudio(filtered.hash);
  assert.equal(restored.documentHash,run);
  assert.equal(restored.drawer.params.get('kind'),'review');
  assert.equal(restored.hash,filtered.hash);
});

test('design and installed deep links select documents, settings and resource searches stay scoped',async()=>{
  const {resolveStudio} = await routes;
  assert.equal(resolveStudio('').documentHash,'canvas');
  assert.equal(resolveStudio('overview').documentHash,'canvas');
  assert.equal(resolveStudio('workflows?view=design').drawer,undefined);
  assert.equal(resolveStudio('workflows/summary?view=canvas').doc.key,'summary');
  assert.equal(resolveStudio('workflows?view=examples','providers').documentHash,'canvas');
  assert.equal(resolveStudio('providers?panel=attention').studio,false);
  assert.equal(resolveStudio('canvas?panel=providers').drawer,undefined);
});
