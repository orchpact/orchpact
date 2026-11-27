// A drawer has its own location. Opening or filtering it leaves the active canvas mounted.
export function readRoute(fragment = '') {
  const raw = fragment.replace(/^#/, '') || 'canvas';
  const split = raw.indexOf('?');
  const path = split < 0 ? raw : raw.slice(0, split);
  const [page, encoded = ''] = path.split('/');
  return {page, key:decodeURIComponent(encoded), params:new URLSearchParams(split < 0 ? '' : raw.slice(split + 1))};
}

function resource(route) {
  return !route.key && (route.page === 'projects' || route.page === 'attention' ||
    (route.page === 'workflows' && route.params.get('view') !== 'design'));
}

function documentRoute(route) {
  return ['canvas', 'workflows', 'projects'].includes(route.page) && !resource(route);
}

export function fragmentOf({page, key, params}) {
  return page + (key ? '/' + encodeURIComponent(key) : '') + (params.size ? '?' + params : '');
}

export function resolveStudio(fragment, previous = 'canvas') {
  const route = readRoute(fragment);
  const requestedPanel = route.params.get('panel');
  route.params.delete('panel');
  let doc = route, drawer;
  if (resource(route)) {
    const prior = readRoute(previous);
    doc = documentRoute(prior) ? prior : readRoute('canvas');
    drawer = route;
  } else if (doc.page === 'overview') {
    doc = readRoute('canvas');
  } else if (documentRoute(doc) && requestedPanel) {
    const candidate = readRoute(requestedPanel);
    if (resource(candidate)) drawer = candidate;
  }
  const documentHash = fragmentOf(doc);
  const canonical = readRoute(documentHash);
  if (drawer) canonical.params.set('panel', fragmentOf(drawer));
  return {doc, drawer, documentHash, hash:fragmentOf(canonical), studio:documentRoute(doc)};
}
