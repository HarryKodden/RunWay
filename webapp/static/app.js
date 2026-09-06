const scenarioList = document.getElementById('scenario-list');
const scenarioEditor = document.getElementById('scenario-editor');
const scenarioNameInput = document.getElementById('scenario-name');
const editorKind = document.getElementById('editor-kind');
const editorTitle = document.getElementById('editor-title');
const editorCrumb = document.getElementById('editor-crumb');
const collectionPanel = document.getElementById('collection-panel');
const collectionSection = document.getElementById('collection-section');
const scenarioSection = document.getElementById('scenario-section');
const saveCollectionButton = document.getElementById('save-collection');
const copyCollectionButton = document.getElementById('copy-collection');
const shareCollectionButton = document.getElementById('share-collection');
const deleteCollectionButton = document.getElementById('delete-collection');
const importCollectionButton = document.getElementById('import-collection');
const importCollectionFileInput = document.getElementById('import-collection-file');
const exportCollectionBrunoButton = document.getElementById('export-collection-bruno');
const deleteScenarioButton = document.getElementById('delete-scenario');
const collectionMemberCount = document.getElementById('collection-member-count');
const collectionDescription = document.getElementById('collection-description');
const collectionMembers = document.getElementById('collection-members');
const scenarioBuilder = document.getElementById('scenario-builder');
const addStepButton = document.getElementById('add-step');
const saveScenarioButton = document.getElementById('save-scenario');
const runTitle = document.getElementById('run-title');
const scenarioBaseUrl = document.getElementById('scenario-base-url');
const scenarioEnvironmentSelect = document.getElementById('scenario-environment');
const editRandomGeneratorsJsonButton = document.getElementById('edit-random-generators-json');
const editEnvironmentsJsonButton = document.getElementById('edit-environments-json');
const jsonDialog = document.getElementById('json-dialog');
const jsonDialogTitle = document.getElementById('json-dialog-title');
const jsonDialogEditor = document.getElementById('json-dialog-editor');
const jsonDialogCancelButton = document.getElementById('json-dialog-cancel');
const jsonDialogCancelTopButton = document.getElementById('json-dialog-cancel-top');
const jsonDialogSaveButton = document.getElementById('json-dialog-save');
const stepSequence = document.getElementById('step-sequence');
const stepCount = document.getElementById('step-count');
const selectedStepLabel = document.getElementById('selected-step-label');
const stepDetailEmpty = document.getElementById('step-detail-empty');
const stepDetailForm = document.getElementById('step-detail-form');
const stepNameInput = document.getElementById('step-name');
const stepMethodInput = document.getElementById('step-method');
const stepPathInput = document.getElementById('step-path');
const stepPathResolved = document.getElementById('step-path-resolved');
const stepTimeoutInput = document.getElementById('step-timeout');
const stepExpectedStatusInput = document.getElementById('step-expected-status');
const stepSaveResponseAsInput = document.getElementById('step-save-response-as');
const editStepHeadersJsonButton = document.getElementById('edit-step-headers-json');
const editStepJsonButton = document.getElementById('edit-step-json');
const editStepSaveJsonButton = document.getElementById('edit-step-save-json');
const editStepExpectedJsonButton = document.getElementById('edit-step-expected-json');
const stepFollowRedirectsInput = document.getElementById('step-follow-redirects');
const stepStopOnFailureInput = document.getElementById('step-stop-on-failure');
const stepMoveUpButton = document.getElementById('step-move-up');
const stepMoveDownButton = document.getElementById('step-move-down');
const stepDeleteButton = document.getElementById('step-delete');
const testStepButton = document.getElementById('test-step');
const testSequenceButton = document.getElementById('test-sequence');
const sequenceTestOutput = document.getElementById('sequence-test-output');
const stepCurlOutput = document.getElementById('step-curl');
const copyStepCurlButton = document.getElementById('copy-step-curl');
const importStepCurlButton = document.getElementById('import-step-curl');
const curlDialog = document.getElementById('curl-dialog');
const curlDialogEditor = document.getElementById('curl-dialog-editor');
const regressionRunButton = document.getElementById('regression-run');
const scenarioBaseUrlWrap = document.getElementById('scenario-base-url-wrap');
const runTargetHint = document.getElementById('run-target-hint');
const THEME_STORAGE_KEY = 'runway.theme';
const THEME_DEFAULT = 'dark';
const ENV_OVERRIDE_STORAGE_KEY = 'runway.env.overrides';
const ENV_REMOVED_STORAGE_KEY = 'runway.env.removed';
const runSpinner = document.getElementById('run-spinner');
const runProgressWrap = document.getElementById('run-progress-wrap');
const runProgressBar = document.getElementById('run-progress-bar');
const runProgressTrack = document.getElementById('run-progress-bar-track');
const runProgressLabel = document.getElementById('run-progress-label');
const runProgressTime = document.getElementById('run-progress-time');
const stepTestOutput = document.getElementById('step-test-output');
const runOutput = document.getElementById('run-output');
const reportText = document.getElementById('report-text');
const reportSummary = document.getElementById('report-summary');
let selectedScenarioName = null;
let selectedFolderPath = '';
let activeCollectionPath = '';
let activeCollectionMembers = [];
let activeCollectionDocument = null;
let currentAccess = null;
let collectionMemberDocs = new Map();
let collectionMemberDocsCollection = '';
let collectionMemberDocsLoading = null;
const LAST_OPEN_STORAGE_KEY = 'runway.last.open';
let scenarioTree = {type: 'dir', name: 'examples', path: '', children: []};
let expandedFolders = new Set();
let expandedCollections = new Set();
let didExpandAllFolders = false;
let currentScenario = null;
let selectedStepIndex = -1;
let draggedStepIndex = -1;
let currentJsonDialogTarget = null;
let lastStepContextVars = {};
let stepCurlTimer = null;
let stepCurlGeneration = 0;
let lastHydratedCurlKey = '';
let lastCopiedCurl = '';
let authState = {authenticated: false, oidc_enabled: false, user: null};
let loadedWorkspaceEnvKey = '';
let envPersistTimer = null;
let explorerDrag = null;
let askDialogResolver = null;
let askDialogValue = '';

function isWorkspacePath(path) {
  return String(path || '').replaceAll('\\', '/').replace(/^\.\//, '').startsWith('workspace/');
}

function canEditActiveCollection() {
  const permission = currentAccess?.permission || findTreeNode(activeCollectionPath)?.permission || 'owner';
  return permission === 'owner' || permission === 'edit';
}

function isOwnerOfActiveCollection() {
  const permission = currentAccess?.permission || findTreeNode(activeCollectionPath)?.permission || 'owner';
  return permission === 'owner';
}

function applyDocumentAccess(document) {
  if (!document || typeof document !== 'object') {
    currentAccess = null;
    return document;
  }
  if (document._access && typeof document._access === 'object') {
    currentAccess = document._access;
  } else if (isWorkspacePath(selectedScenarioName || activeCollectionPath)) {
    const node = findTreeNode(activeCollectionPath || selectedScenarioName);
    if (node?.permission) {
      currentAccess = {
        permission: node.permission,
        owner_id: node.owner_id || '',
        owner_name: node.owner_name || '',
        source_collection_id: node.source_collection_id || '',
      };
    }
  } else {
    currentAccess = null;
  }
  const cleaned = {...document};
  delete cleaned._access;
  return cleaned;
}

function isWorkspaceFolderPath(path) {
  const raw = String(path || '');
  return raw === 'workspace' || raw.startsWith('ws-folder/');
}

function isCollectionFileName(path) {
  const name = String(path || '').split('/').pop();
  return name === 'collection.json' || name === 'suite.json';
}

function collectionDocumentFilename(path) {
  const name = String(path || '').split('/').pop();
  return name === 'suite.json' ? 'suite.json' : 'collection.json';
}

function workspaceCollectionId(path) {
  const parts = String(path || '').replaceAll('\\', '/').split('/').filter(Boolean);
  return parts[0] === 'workspace' && parts[1] ? parts[1] : '';
}

function workspaceFilePath(collectionId, filename) {
  return `workspace/${collectionId}/${filename}`;
}

function requireSignInMessage() {
  return authState.oidc_enabled
    ? 'Sign in to save or import into your workspace.'
    : 'A workspace user is required.';
}

async function api(path, options = {}) {
  const {headers: extraHeaders, cache: _ignoredCache, ...rest} = options;
  const method = String(rest.method || 'GET').toUpperCase();
  let url = path;
  if (method === 'GET') {
    url += `${path.includes('?') ? '&' : '?'}_=${Date.now()}`;
  }
  const response = await fetch(url, {
    cache: 'no-store',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', ...extraHeaders},
    ...rest,
  });
  if (response.status === 401 && authState.oidc_enabled) {
    window.location.href = '/login';
    throw new Error('Sign in required');
  }
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `Request failed: ${response.status}`);
  }
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    return response.json();
  }
  return response.text();
}

function parentFolderPath(filePath) {
  const index = filePath.lastIndexOf('/');
  return index === -1 ? '' : filePath.slice(0, index);
}

function joinPath(...parts) {
  return parts.filter(Boolean).join('/').replaceAll(/\/+/g, '/');
}

function scenarioFileUrl(path) {
  if (isWorkspacePath(path)) {
    return `/api/workspace/file?path=${encodeURIComponent(path)}`;
  }
  return `/api/scenarios/file?path=${encodeURIComponent(path)}`;
}

function parentCollectionUrl(path) {
  if (isWorkspacePath(path)) {
    return `/api/workspace/parent-collection?path=${encodeURIComponent(path)}`;
  }
  return `/api/scenarios/parent-collection?path=${encodeURIComponent(path)}`;
}

function expandAncestorFolders(filePath) {
  const parts = filePath.split('/').filter(Boolean);
  let current = '';
  parts.slice(0, -1).forEach((part) => {
    current = joinPath(current, part);
    expandedFolders.add(current);
  });
}

function expandFolderPath(folderPath) {
  if (!folderPath) {
    return;
  }
  if (folderPath === 'shared') {
    expandedFolders.add('shared');
    return;
  }
  if (isWorkspaceFolderPath(folderPath) || String(folderPath).startsWith('ws-folder/')) {
    expandedFolders.add('workspace');
  }
  const parts = folderPath.split('/').filter(Boolean);
  let current = '';
  parts.forEach((part) => {
    current = joinPath(current, part);
    expandedFolders.add(current);
  });
}

function collectFolderPaths(nodes, into = new Set()) {
  for (const node of nodes || []) {
    if (node.type === 'dir') {
      into.add(node.path);
      collectFolderPaths(node.children, into);
    }
  }
  return into;
}

function isCollectionNode(node) {
  if (!node || node.type === 'dir') {
    return false;
  }
  return node.kind === 'collection' || node.kind === 'suite' || (node.member_count || 0) > 0 || isCollectionFileName(node.path);
}

function findFirstCollectionFile(nodes) {
  for (const node of nodes || []) {
    if (node.type === 'file' && isCollectionNode(node)) {
      return node;
    }
    if (node.type === 'dir') {
      const nested = findFirstCollectionFile(node.children);
      if (nested) {
        return nested;
      }
    }
  }
  return null;
}

function svgIcon(name) {
  if (name === 'chevron') {
    return '<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M6.2 3.2a.75.75 0 0 1 1.06 0l4 4a.75.75 0 0 1 0 1.06l-4 4A.75.75 0 0 1 6.2 11.2L9.4 8 6.2 4.8a.75.75 0 0 1 0-1.6z"/></svg>';
  }
  if (name === 'folder') {
    return '<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M2.5 3.5A1.5 1.5 0 0 1 4 2h2.2c.4 0 .77.2 1 .53L8.2 4H12a1.5 1.5 0 0 1 1.5 1.5v7A1.5 1.5 0 0 1 12 14H4A1.5 1.5 0 0 1 2.5 12.5v-9z"/></svg>';
  }
  if (name === 'collection') {
    return '<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M3 3.5h10v2H3v-2zm0 3.5h10v2H3V7zm0 3.5h10V13H3v-2z"/></svg>';
  }
  if (name === 'more') {
    return '<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M3 8.75A1.25 1.25 0 1 1 3 6.25 1.25 1.25 0 0 1 3 8.75zm5 0A1.25 1.25 0 1 1 8 6.25 1.25 1.25 0 0 1 8 8.75zm5 0A1.25 1.25 0 1 1 13 6.25 1.25 1.25 0 0 1 13 8.75z"/></svg>';
  }
  return '<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M4.5 2A1.5 1.5 0 0 0 3 3.5v9A1.5 1.5 0 0 0 4.5 14h7A1.5 1.5 0 0 0 13 12.5V6.2c0-.4-.16-.78-.44-1.06l-2.7-2.7A1.5 1.5 0 0 0 8.8 2H4.5z"/></svg>';
}

function findTreeNode(path, nodes = scenarioTree?.children) {
  for (const node of nodes || []) {
    if (node.path === path) {
      return node;
    }
    if (node.type === 'dir') {
      const nested = findTreeNode(path, node.children);
      if (nested) {
        return nested;
      }
    }
  }
  return null;
}

function collectCollectionNodes(nodes = scenarioTree?.children, into = []) {
  for (const node of nodes || []) {
    if (node.type === 'dir') {
      collectCollectionNodes(node.children, into);
    } else if (isCollectionNode(node)) {
      into.push(node);
    }
  }
  return into;
}

function folderPathForCollection(node) {
  if (!node) {
    return 'workspace';
  }
  if (node.source === 'shared') {
    return 'shared';
  }
  if (isWorkspacePath(node.path)) {
    return node.folder ? `ws-folder/${node.folder}` : 'workspace';
  }
  return parentFolderPath(node.path) || 'examples';
}

function explorerCreateContext() {
  const selected = findTreeNode(selectedFolderPath);
  const openCollection = findTreeNode(activeCollectionPath) || findTreeNode(selectedScenarioName);
  let folderPath = 'workspace';
  if (selected && selected.type === 'dir' && selected.source === 'workspace') {
    folderPath = selected.path;
  } else if (openCollection && isCollectionNode(openCollection) && isWorkspacePath(openCollection.path)) {
    folderPath = folderPathForCollection(openCollection);
  }
  const folderNode = findTreeNode(folderPath);
  const collectionPath = openCollection && isCollectionNode(openCollection) && isWorkspacePath(openCollection.path)
    ? openCollection.path
    : (activeCollectionPath && isWorkspacePath(activeCollectionPath) ? activeCollectionPath : '');
  const collectionNode = collectionPath ? findTreeNode(collectionPath) : null;
  return {
    folderPath,
    folderLabel: folderNode?.name || 'My workspace',
    collectionPath,
    collectionLabel: collectionNode?.name || fileLabel(collectionPath),
  };
}

function updateNewMenuHint() {
  const hint = document.getElementById('new-item-hint');
  if (!hint) {
    return;
  }
  const ctx = explorerCreateContext();
  hint.textContent = ctx.collectionPath
    ? `Collection/folder in ${ctx.folderLabel}. Scenario in ${ctx.collectionLabel}.`
    : `Creates in ${ctx.folderLabel}`;
}

function renderExplorer() {
  scenarioList.innerHTML = '';
  const nodes = scenarioTree?.children || [];
  if (nodes.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'file-explorer-empty';
    empty.textContent = 'No collections yet. Open the public library or copy a collection into your workspace.';
    scenarioList.appendChild(empty);
    return;
  }
  renderTreeNodes(scenarioList, nodes, 0, '');
  updateNewMenuHint();
}

function stopRowGesture(event) {
  event.stopPropagation();
}

function moreButton(target) {
  if (explorerMenuItems(target).length === 0) {
    return document.createTextNode('');
  }
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'tree-more';
  button.title = 'Actions';
  button.setAttribute('aria-label', 'Actions');
  button.draggable = false;
  button.innerHTML = svgIcon('more');
  button.addEventListener('pointerdown', stopRowGesture);
  button.addEventListener('mousedown', stopRowGesture);
  button.addEventListener('click', (event) => {
    stopRowGesture(event);
    showExplorerMenu(event, target, button);
  });
  return button;
}

function bindRowMenu(row, target) {
  row.addEventListener('contextmenu', (event) => {
    event.preventDefault();
    selectedFolderPath = target.kind === 'folder' ? target.path : target.parent || selectedFolderPath;
    showExplorerMenu(event, target);
  });
}

function bindRowDrag(row, target, handle) {
  if (!target.draggable || !handle) {
    return;
  }
  row.classList.add('is-draggable');
  handle.draggable = true;
  handle.addEventListener('dragstart', (event) => {
    explorerDrag = target;
    row.classList.add('dragging');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', target.path);
  });
  handle.addEventListener('dragend', () => {
    explorerDrag = null;
    scenarioList.querySelectorAll('.tree-row.dragging, .tree-row.drag-over, .tree-row.drop-folder').forEach((item) => {
      item.classList.remove('dragging', 'drag-over', 'drop-folder');
    });
  });
}

function bindRowDrop(row, target) {
  row.addEventListener('dragover', (event) => {
    if (!explorerDrag || explorerDrag.path === target.path) {
      return;
    }
    const canReorder = explorerDrag.kind === target.kind && explorerDrag.parent === target.parent && explorerDrag.draggable;
    const canMoveCollection = explorerDrag.kind === 'collection' && target.kind === 'folder' && target.source === 'workspace' && isWorkspacePath(explorerDrag.path);
    const canMoveFolder = explorerDrag.kind === 'folder' && target.kind === 'folder' && target.source === 'workspace' && explorerDrag.source === 'workspace' && explorerDrag.path !== target.path && !String(target.path).startsWith(`${explorerDrag.path}/`);
    if (!canReorder && !canMoveCollection && !canMoveFolder) {
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    row.classList.toggle('drop-folder', canMoveCollection || canMoveFolder);
    row.classList.toggle('drag-over', canReorder && !canMoveCollection && !canMoveFolder);
  });
  row.addEventListener('dragleave', () => {
    row.classList.remove('drag-over', 'drop-folder');
  });
  row.addEventListener('drop', (event) => {
    event.preventDefault();
    row.classList.remove('drag-over', 'drop-folder');
    if (!explorerDrag || explorerDrag.path === target.path) {
      return;
    }
    if (explorerDrag.kind === 'folder' && target.kind === 'folder' && target.source === 'workspace') {
      void moveFolderToFolder(explorerDrag.path, target.path);
      return;
    }
    if (explorerDrag.kind === 'collection' && target.kind === 'folder' && target.source === 'workspace') {
      void moveCollectionToFolder(explorerDrag.path, target.path);
      return;
    }
    if (explorerDrag.kind === target.kind && explorerDrag.parent === target.parent) {
      void reorderDropped(explorerDrag, target);
    }
  });
}

function renderTreeNodes(container, nodes, depth, parentPath) {
  nodes.forEach((node) => {
    if (node.type === 'dir') {
      const expanded = expandedFolders.has(node.path);
      const workspaceFolder = node.source === 'workspace' && node.path.startsWith('ws-folder/');
      const target = {
        kind: 'folder',
        path: node.path,
        name: node.name,
        parent: parentPath,
        source: node.source || 'library',
        draggable: workspaceFolder,
      };
      const row = document.createElement('div');
      row.className = `tree-row${node.path === selectedFolderPath ? ' folder-selected' : ''}`;
      row.style.paddingLeft = `${8 + depth * 14}px`;
      row.title = node.path || node.name;

      const chevron = document.createElement('button');
      chevron.type = 'button';
      chevron.className = 'tree-chevron';
      chevron.innerHTML = svgIcon('chevron');
      chevron.style.transform = expanded ? 'rotate(90deg)' : 'rotate(0deg)';
      chevron.setAttribute('aria-label', expanded ? 'Collapse folder' : 'Expand folder');
      chevron.addEventListener('pointerdown', stopRowGesture);
      chevron.addEventListener('mousedown', stopRowGesture);
      chevron.addEventListener('click', (event) => {
        event.stopPropagation();
        if (expandedFolders.has(node.path)) {
          expandedFolders.delete(node.path);
        } else {
          expandedFolders.add(node.path);
        }
        renderExplorer();
      });

      const icon = document.createElement('span');
      icon.className = 'tree-icon';
      icon.innerHTML = svgIcon('folder');

      const label = document.createElement('span');
      label.className = 'tree-label';
      label.textContent = node.name;
      if (node.source === 'shared') {
        row.classList.add('is-shared');
        const badge = document.createElement('span');
        badge.className = 'tree-badge';
        badge.textContent = 'Shared';
        row.append(chevron, icon, label, badge, moreButton(target));
      } else {
        row.append(chevron, icon, label, moreButton(target));
      }
      row.addEventListener('click', () => {
        selectedFolderPath = node.path;
        expandedFolders.add(node.path);
        renderExplorer();
      });
      bindRowMenu(row, target);
      bindRowDrag(row, target, label);
      bindRowDrop(row, target);
      container.appendChild(row);

      if (expanded) {
        renderTreeNodes(container, node.children || [], depth + 1, node.path);
      }
      return;
    }

    if (!isCollectionNode(node)) {
      return;
    }
    const expanded = expandedCollections.has(node.path);
    const selected = node.path === selectedScenarioName || node.path === activeCollectionPath;
    const parent = node.source === 'workspace' ? folderPathForCollection(node) : parentPath;
    const target = {
      kind: 'collection',
      path: node.path,
      name: node.name,
      parent,
      source: node.source || (isWorkspacePath(node.path) ? 'workspace' : 'library'),
      folder: node.folder || '',
      permission: node.permission || (node.source === 'shared' ? 'read' : 'owner'),
      owner_name: node.owner_name || '',
      owner_id: node.owner_id || '',
      source_collection_id: node.source_collection_id || '',
      draggable: node.source === 'workspace' && isWorkspacePath(node.path),
    };
    const row = document.createElement('div');
    row.className = `tree-row kind-collection${selected ? ' selected' : ''}`;
    row.style.paddingLeft = `${8 + depth * 14}px`;
    row.title = node.source === 'shared' && node.owner_name
      ? `${node.path} · shared by ${node.owner_name}`
      : node.path;

    const chevron = document.createElement('button');
    chevron.type = 'button';
    chevron.className = 'tree-chevron';
    chevron.innerHTML = svgIcon('chevron');
    chevron.style.transform = expanded ? 'rotate(90deg)' : 'rotate(0deg)';
    chevron.setAttribute('aria-label', expanded ? 'Collapse collection' : 'Expand collection');
      chevron.addEventListener('pointerdown', stopRowGesture);
      chevron.addEventListener('mousedown', stopRowGesture);
      chevron.addEventListener('click', (event) => {
        event.stopPropagation();
        if (expandedCollections.has(node.path)) {
          expandedCollections.delete(node.path);
        } else {
          expandedCollections.add(node.path);
        }
        renderExplorer();
      });

    const icon = document.createElement('span');
    icon.className = 'tree-icon';
    icon.innerHTML = svgIcon('collection');

    const kind = document.createElement('span');
    kind.className = 'tree-kind';
    kind.textContent = node.source === 'shared' ? 'Shared' : 'Collection';

    const label = document.createElement('span');
    label.className = 'tree-label';
    label.textContent = node.source === 'shared' && node.owner_name
      ? `${node.name} · ${node.owner_name}`
      : node.name;
    if (node.source === 'shared') {
      row.classList.add('is-shared');
    }

    const meta = document.createElement('span');
    meta.className = 'tree-meta';
    const count = node.member_count ?? (node.members || []).length;
    meta.textContent = `${count}`;
    meta.title = `${count} scenario${count === 1 ? '' : 's'}`;

    row.append(chevron, icon, kind, label, meta, moreButton(target));
    row.addEventListener('click', () => {
      selectedFolderPath = folderPathForCollection(node);
      expandedCollections.add(node.path);
      void openScenario(node);
    });
    bindRowMenu(row, target);
    bindRowDrag(row, target, label);
    bindRowDrop(row, target);
    container.appendChild(row);

    if (!expanded) {
      return;
    }
    const folder = parentFolderPath(node.path);
    (node.members || []).forEach((memberName) => {
      const memberPath = joinPath(folder, memberName);
      const memberTarget = {
        kind: 'scenario',
        path: memberPath,
        name: memberName,
        parent: node.path,
        source: target.source,
        permission: target.permission,
        draggable: target.source === 'workspace' && isWorkspacePath(memberPath),
      };
      const memberRow = document.createElement('div');
      memberRow.className = `tree-row kind-scenario${memberPath === selectedScenarioName ? ' selected' : ''}`;
      memberRow.style.paddingLeft = `${8 + (depth + 1) * 14 + 22}px`;
      memberRow.title = memberPath;

      const memberIcon = document.createElement('span');
      memberIcon.className = 'tree-icon';
      memberIcon.innerHTML = svgIcon('file');

      const memberKind = document.createElement('span');
      memberKind.className = 'tree-kind';
      memberKind.textContent = 'Scenario';

      const memberLabel = document.createElement('span');
      memberLabel.className = 'tree-label';
      memberLabel.textContent = fileLabel(memberName);

      memberRow.append(memberIcon, memberKind, memberLabel, moreButton(memberTarget));
      memberRow.addEventListener('click', () => {
        void openCollectionMember(node, memberName);
      });
      bindRowMenu(memberRow, memberTarget);
      bindRowDrag(memberRow, memberTarget, memberLabel);
      bindRowDrop(memberRow, memberTarget);
      container.appendChild(memberRow);
    });
  });
}

function createEmptyScenario() {
  return {
    base_url: '',
    environments: {},
    selected_environment: '',
    random_generators: {},
    steps: [],
  };
}

function createEmptyStep() {
  return {
    name: 'new_step',
    method: 'GET',
    path: '/health',
    expected_status: 200,
  };
}

function normalizeScenario(scenario) {
  const normalizedEnvironments = scenario?.environments && typeof scenario.environments === 'object' && !Array.isArray(scenario.environments)
    ? scenario.environments
    : {};
  const selectedEnvironment = typeof scenario?.selected_environment === 'string'
    ? scenario.selected_environment
    : '';

  return {
    ...createEmptyScenario(),
    ...scenario,
    environments: normalizedEnvironments,
    selected_environment: selectedEnvironment,
    random_generators: scenario?.random_generators || {},
    steps: Array.isArray(scenario?.steps) ? scenario.steps : [],
  };
}

function isDefinedValue(value) {
  return value !== undefined && value !== null && !(typeof value === 'string' && value.trim() === '');
}

function mergeDefined(higher, lower) {
  const merged = {...(lower || {})};
  Object.entries(higher || {}).forEach(([key, value]) => {
    if (!isDefinedValue(value)) {
      return;
    }
    const current = merged[key];
    if (
      value && typeof value === 'object' && !Array.isArray(value)
      && current && typeof current === 'object' && !Array.isArray(current)
    ) {
      merged[key] = mergeDefined(value, current);
      return;
    }
    merged[key] = value;
  });
  return merged;
}

function getChildEnvironments() {
  if (!currentScenario || typeof currentScenario.environments !== 'object' || Array.isArray(currentScenario.environments)) {
    return {};
  }
  return currentScenario.environments;
}

function getCollectionEnvironments() {
  const collectionEnvs = activeCollectionDocument?.environments;
  if (!collectionEnvs || typeof collectionEnvs !== 'object' || Array.isArray(collectionEnvs)) {
    return {};
  }
  return collectionEnvs;
}

function getScenarioEnvironments() {
  const collectionEnvs = getCollectionEnvironments();
  if (Object.keys(collectionEnvs).length > 0 && !isCollectionDocument(currentScenario)) {
    return collectionEnvs;
  }
  if (isCollectionDocument(currentScenario)) {
    return getChildEnvironments();
  }
  return Object.keys(collectionEnvs).length > 0 ? collectionEnvs : getChildEnvironments();
}

function getSelectedEnvironmentName() {
  if (!currentScenario) {
    return '';
  }
  const environments = getScenarioEnvironments();
  const collectionSelected = activeCollectionDocument?.selected_environment || '';
  const selected = currentScenario.selected_environment || collectionSelected || '';
  if (selected && environments[selected]) {
    return selected;
  }
  if (collectionSelected && environments[collectionSelected]) {
    return collectionSelected;
  }
  return '';
}

const CONNECTION_ENV_KEYS = new Set(['server', 'base_url', 'baseUrl', 'url', 'mock_provider']);
const FORBIDDEN_API_HOSTS = new Set([
  'localhost',
  '127.0.0.1',
  '::1',
  '0.0.0.0',
  'host.docker.internal',
  'ip6-localhost',
  'ip6-loopback',
]);
const ROUTABLE_HOST_HELP = 'API hosts must be an IP or FQDN, not localhost';

function hostnameFromTarget(value) {
  const text = String(value || '').trim();
  if (!text) {
    return '';
  }
  const parsed = parseRunUrl(text.includes('://') ? text : `http://${text}`);
  return (parsed?.host || '').toLowerCase();
}

function isForbiddenApiHost(host) {
  const name = String(host || '').trim().toLowerCase().replace(/^\[|\]$/g, '').replace(/\.$/, '');
  if (!name) {
    return false;
  }
  if (FORBIDDEN_API_HOSTS.has(name)) {
    return true;
  }
  return name.endsWith('.localhost');
}

function isForbiddenApiTarget(value) {
  if (typeof value !== 'string') {
    return false;
  }
  const text = value.trim();
  if (!text || text.includes('{{')) {
    return false;
  }
  return isForbiddenApiHost(hostnameFromTarget(text));
}

function unquotePlaceholderDefault(value) {
  const text = String(value || '').trim();
  if (text.length >= 2 && text[0] === text[text.length - 1] && (text[0] === '"' || text[0] === "'")) {
    return text.slice(1, -1);
  }
  return text;
}

function parsePlaceholderToken(raw) {
  const text = String(raw || '').trim();
  const match = text.match(/^([A-Za-z_][\w.]*)(?:\s*:\s*([\s\S]+))?$/);
  if (!match) {
    return {name: text, defaultValue: null};
  }
  return {
    name: match[1],
    defaultValue: match[2] == null ? null : unquotePlaceholderDefault(match[2]),
  };
}

function envPlaceholderPath(token) {
  const {name} = parsePlaceholderToken(token);
  if (!name || name.startsWith('vars.') || name.startsWith('random.') || name.startsWith('meta.')) {
    return null;
  }
  if (name.startsWith('env.')) {
    return name.slice(4);
  }
  if (name.startsWith('_.')) {
    return name.slice(2);
  }
  return name;
}

function placeholderDefault(token) {
  return parsePlaceholderToken(token).defaultValue;
}

function valueHasEnvPlaceholders(value) {
  const matches = String(value).match(/\{\{\s*([^}]+)\s*\}\}/g) || [];
  return matches.some((match) => {
    const inner = match.replace(/^\{\{\s*|\s*\}\}$/g, '');
    return envPlaceholderPath(inner) != null;
  });
}

function expandEnvironmentValues(env, keys) {
  if (!env || typeof env !== 'object' || Array.isArray(env)) {
    return {};
  }
  let current = {...env};

  const expandString = (value) => value.replace(/\{\{\s*([^}]+)\s*\}\}/g, (match, rawToken) => {
    const path = envPlaceholderPath(rawToken);
    const defaultValue = placeholderDefault(rawToken);
    if (path == null) {
      return match;
    }
    let resolved = lookupEnvPath(current, path);
    if (resolved == null && Object.prototype.hasOwnProperty.call(current, path)) {
      resolved = current[path];
    }
    if (resolved == null || resolved === '' || typeof resolved === 'object') {
      return defaultValue != null ? defaultValue : match;
    }
    const text = String(resolved);
    if (valueHasEnvPlaceholders(text)) {
      return defaultValue != null ? defaultValue : match;
    }
    return text;
  });

  const walk = (value) => {
    if (typeof value === 'string') {
      return expandString(value);
    }
    if (Array.isArray(value)) {
      return value.map(walk);
    }
    if (value && typeof value === 'object') {
      const next = {};
      Object.entries(value).forEach(([key, item]) => {
        next[key] = walk(item);
      });
      return next;
    }
    return value;
  };

  for (let i = 0; i < 10; i += 1) {
    let next;
    if (keys && keys.size) {
      next = {...current};
      keys.forEach((key) => {
        if (Object.prototype.hasOwnProperty.call(next, key)) {
          next[key] = walk(next[key]);
        }
      });
    } else {
      next = walk(current);
    }
    if (JSON.stringify(next) === JSON.stringify(current)) {
      break;
    }
    current = next;
  }
  return current;
}

function isConcreteEnvValue(value) {
  if (value == null) {
    return false;
  }
  if (typeof value === 'string') {
    const text = value.trim();
    return Boolean(text) && !valueHasEnvPlaceholders(text);
  }
  if (typeof value === 'object') {
    return Array.isArray(value) ? value.length > 0 : Object.keys(value).length > 0;
  }
  return true;
}

function collectEnvRefSpecs(value, found = new Map()) {
  if (typeof value === 'string') {
    const pattern = /\{\{\s*([^}]+)\s*\}\}/g;
    let match = pattern.exec(value);
    while (match) {
      const path = envPlaceholderPath(match[1]);
      if (path) {
        const defaultValue = placeholderDefault(match[1]);
        if (!found.has(path)) {
          found.set(path, defaultValue);
        } else if (defaultValue == null) {
          found.set(path, null);
        }
      }
      match = pattern.exec(value);
    }
  } else if (Array.isArray(value)) {
    value.forEach((item) => collectEnvRefSpecs(item, found));
  } else if (value && typeof value === 'object') {
    Object.values(value).forEach((item) => collectEnvRefSpecs(item, found));
  }
  return found;
}

function collectEnvRefs(value, found = new Set()) {
  collectEnvRefSpecs(value).forEach((defaultValue, path) => {
    if (defaultValue == null) {
      found.add(path);
    }
  });
  return found;
}

function expandPlaceholderDefaults(value) {
  return String(value || '').replace(/\{\{\s*([^}]+)\s*\}\}/g, (match, rawToken) => {
    const defaultValue = placeholderDefault(rawToken);
    return defaultValue != null ? defaultValue : match;
  });
}

function getRawMergedEnvironment() {
  const selectedName = getSelectedEnvironmentName();
  if (!selectedName) {
    return {};
  }
  const higher = getCollectionEnvironments()[selectedName];
  const lower = getChildEnvironments()[selectedName];
  return mergeDefined(
    higher && typeof higher === 'object' ? higher : {},
    lower && typeof lower === 'object' ? lower : {},
  );
}

function setEnvPath(target, path, value) {
  const parts = String(path).split('.');
  let cursor = target;
  for (let i = 0; i < parts.length - 1; i += 1) {
    const part = parts[i];
    if (!cursor[part] || typeof cursor[part] !== 'object' || Array.isArray(cursor[part])) {
      cursor[part] = {};
    }
    cursor = cursor[part];
  }
  cursor[parts[parts.length - 1]] = value;
}

function loadAllSessionEnvOverrides() {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(ENV_OVERRIDE_STORAGE_KEY) || '{}');
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function getSessionEnvOverrides() {
  const all = loadAllSessionEnvOverrides();
  const scoped = all[getSelectedEnvironmentName() || '_'];
  return scoped && typeof scoped === 'object' && !Array.isArray(scoped) ? {...scoped} : {};
}

function saveSessionEnvOverrides(values) {
  const all = loadAllSessionEnvOverrides();
  const key = getSelectedEnvironmentName() || '_';
  const next = {};
  Object.entries(values || {}).forEach(([name, value]) => {
    if (String(value || '').trim()) {
      next[name] = String(value);
    }
  });
  if (Object.keys(next).length) {
    all[key] = next;
  } else {
    delete all[key];
  }
  try {
    sessionStorage.setItem(ENV_OVERRIDE_STORAGE_KEY, JSON.stringify(all));
  } catch {
    // Ignore storage failures; values still apply for this page lifetime via the form.
  }
}

function setSessionEnvOverride(name, value) {
  const current = getSessionEnvOverrides();
  if (String(value || '').trim()) {
    current[name] = String(value);
  } else {
    delete current[name];
  }
  saveSessionEnvOverrides(current);
  scheduleWorkspaceEnvPersist();
}

function clearSessionEnvOverrides() {
  saveSessionEnvOverrides({});
  scheduleWorkspaceEnvPersist();
}

function scheduleWorkspaceEnvPersist() {
  if (!isWorkspacePath(activeCollectionPath || selectedScenarioName || '')) {
    return;
  }
  clearTimeout(envPersistTimer);
  envPersistTimer = setTimeout(() => {
    void persistWorkspaceEnvValues();
  }, 400);
}

async function persistWorkspaceEnvValues() {
  const collectionId = workspaceCollectionId(activeCollectionPath || selectedScenarioName || '');
  const environment = getSelectedEnvironmentName();
  if (!collectionId || !environment || !authState.authenticated) {
    return;
  }
  await api(`/api/workspace/collections/${encodeURIComponent(collectionId)}/env`, {
    method: 'PUT',
    body: JSON.stringify({
      environment,
      values: environmentOverridesPayload(),
    }),
  });
}

async function loadWorkspaceEnvValues() {
  const collectionId = workspaceCollectionId(activeCollectionPath || selectedScenarioName || '');
  const environment = getSelectedEnvironmentName();
  const key = `${collectionId}::${environment || '_'}`;
  if (!collectionId || !authState.authenticated) {
    return;
  }
  if (key === loadedWorkspaceEnvKey) {
    return;
  }
  const data = await api(`/api/workspace/collections/${encodeURIComponent(collectionId)}/env?environment=${encodeURIComponent(environment)}`);
  saveSessionEnvOverrides(data.values || {});
  loadedWorkspaceEnvKey = key;
  lastRequiredEnvNames = '';
}

function applySessionEnvOverrides(env) {
  const next = {...(env || {})};
  Object.entries(getSessionEnvOverrides()).forEach(([key, value]) => {
    if (!isConcreteEnvValue(value)) {
      return;
    }
    if (key.includes('.')) {
      const current = lookupEnvPath(next, key);
      if (!isConcreteEnvValue(current) || isForbiddenApiTarget(current)) {
        setEnvPath(next, key, value);
      }
      return;
    }
    const current = next[key];
    if (!isConcreteEnvValue(current) || (CONNECTION_ENV_KEYS.has(key) && isForbiddenApiTarget(current))) {
      next[key] = value;
    }
  });
  return next;
}

function isAssignedEnvValue(value) {
  if (value == null) {
    return false;
  }
  if (typeof value === 'string') {
    return Boolean(value.trim());
  }
  if (typeof value === 'object') {
    return Array.isArray(value) ? value.length > 0 : Object.keys(value).length > 0;
  }
  return true;
}

function selectedEnvironmentBlock() {
  const name = getSelectedEnvironmentName();
  if (!name) {
    return {};
  }
  const collectionBlock = getCollectionEnvironments()[name];
  if (collectionBlock && typeof collectionBlock === 'object' && !Array.isArray(collectionBlock)) {
    return collectionBlock;
  }
  const childBlock = getChildEnvironments()[name];
  return childBlock && typeof childBlock === 'object' && !Array.isArray(childBlock) ? childBlock : {};
}

function environmentBlock(environments, name) {
  const block = environments && name ? environments[name] : null;
  return block && typeof block === 'object' && !Array.isArray(block) ? block : {};
}

function shouldPromptForEnvKey(key) {
  const name = String(key || '').trim();
  if (!name || name.endsWith('_encoded')) {
    return false;
  }
  return true;
}

function removedEnvStorageScope() {
  return `${activeCollectionPath || selectedScenarioName || '_'}::${getSelectedEnvironmentName() || '_'}`;
}

function loadRemovedEnvKeys() {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(ENV_REMOVED_STORAGE_KEY) || '{}');
    const list = parsed && typeof parsed === 'object' ? parsed[removedEnvStorageScope()] : null;
    return Array.isArray(list)
      ? list.filter((key) => typeof key === 'string' && shouldPromptForEnvKey(key))
      : [];
  } catch {
    return [];
  }
}

function saveRemovedEnvKeys(keys) {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(ENV_REMOVED_STORAGE_KEY) || '{}');
    const all = parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
    const unique = [...new Set((keys || []).filter((key) => shouldPromptForEnvKey(key)))];
    if (unique.length) {
      all[removedEnvStorageScope()] = unique;
    } else {
      delete all[removedEnvStorageScope()];
    }
    sessionStorage.setItem(ENV_REMOVED_STORAGE_KEY, JSON.stringify(all));
  } catch {
    // Ignore storage failures; removed keys still apply for this page lifetime.
  }
}

function syncRemovedEnvKeys(previousEnvironments, nextEnvironments) {
  const name = getSelectedEnvironmentName();
  const previous = environmentBlock(previousEnvironments, name);
  const next = environmentBlock(nextEnvironments, name);
  const removed = new Set(loadRemovedEnvKeys());
  Object.keys(previous).forEach((key) => {
    if (!Object.prototype.hasOwnProperty.call(next, key)) {
      removed.add(key);
    }
  });
  Object.keys(next).forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(next, key)) {
      removed.delete(key);
    }
  });
  saveRemovedEnvKeys([...removed]);
}

function unsetEnvironmentKeys(applySession) {
  const fileBlock = selectedEnvironmentBlock();
  let env = getRawMergedEnvironment();
  if (applySession) {
    env = applyEncodedCompanions(expandEnvironmentValues(applySessionEnvOverrides(env)));
  }
  const keys = new Set([...Object.keys(fileBlock), ...loadRemovedEnvKeys()]);
  return [...keys].filter((key) => {
    if (!shouldPromptForEnvKey(key)) {
      return false;
    }
    if (Object.prototype.hasOwnProperty.call(fileBlock, key) && !isAssignedEnvValue(fileBlock[key])) {
      return applySession ? !isConcreteEnvValue(env[key]) : true;
    }
    if (!Object.prototype.hasOwnProperty.call(fileBlock, key)) {
      return applySession ? !isConcreteEnvValue(env[key]) : true;
    }
    return false;
  }).sort();
}

function activeCollectionFilePath() {
  if (activeCollectionPath) {
    return activeCollectionPath;
  }
  if (isCollectionDocument(currentScenario) && selectedScenarioName) {
    return selectedScenarioName;
  }
  return '';
}

function collectionMemberScenarioPaths() {
  const collectionPath = activeCollectionFilePath();
  if (!collectionPath) {
    return [];
  }
  const members = isCollectionDocument(currentScenario)
    ? (Array.isArray(currentScenario.scenarios) ? currentScenario.scenarios : [])
    : activeCollectionMembers.slice();
  const folder = parentFolderPath(collectionPath);
  return members
    .map((name) => joinPath(folder, String(name)))
    .filter(Boolean);
}

function syncCurrentScenarioIntoMemberDocs() {
  if (!selectedScenarioName || !currentScenario || isCollectionDocument(currentScenario)) {
    return;
  }
  const members = new Set(collectionMemberScenarioPaths());
  if (members.size > 0 && !members.has(selectedScenarioName)) {
    return;
  }
  collectionMemberDocs.set(selectedScenarioName, currentScenario);
}

function clearCollectionMemberDocs(collectionPath = '') {
  collectionMemberDocs.clear();
  collectionMemberDocsCollection = collectionPath || '';
  lastRequiredEnvNames = '';
}

function envScanSteps() {
  syncCurrentScenarioIntoMemberDocs();
  const allowed = new Set(collectionMemberScenarioPaths());
  const steps = [];
  if (currentScenario && !isCollectionDocument(currentScenario) && Array.isArray(currentScenario.steps)) {
    steps.push(...currentScenario.steps);
  }
  collectionMemberDocs.forEach((doc, path) => {
    if (allowed.size > 0 && !allowed.has(path)) {
      return;
    }
    if (allowed.size === 0 && path !== selectedScenarioName) {
      return;
    }
    if (path === selectedScenarioName && currentScenario && !isCollectionDocument(currentScenario)) {
      return;
    }
    if (doc && Array.isArray(doc.steps)) {
      steps.push(...doc.steps);
    }
  });
  return steps;
}

async function ensureCollectionMemberDocs() {
  const collectionPath = activeCollectionFilePath();
  const memberPaths = collectionMemberScenarioPaths();
  if (!collectionPath) {
    clearCollectionMemberDocs();
    return false;
  }
  if (collectionMemberDocsCollection !== collectionPath) {
    clearCollectionMemberDocs(collectionPath);
  }
  // Drop cached scenarios that are not members of the open collection (empty collection → clear all).
  const allowed = new Set(memberPaths);
  [...collectionMemberDocs.keys()].forEach((path) => {
    if (!allowed.has(path)) {
      collectionMemberDocs.delete(path);
    }
  });
  if (memberPaths.length === 0) {
    return true;
  }
  collectionMemberDocsCollection = collectionPath;
  syncCurrentScenarioIntoMemberDocs();
  const missing = memberPaths.filter((path) => !collectionMemberDocs.has(path));
  if (missing.length === 0) {
    return false;
  }
  const loaded = await Promise.all(missing.map(async (path) => {
    try {
      const document = await api(scenarioFileUrl(path));
      return [path, normalizeScenario(document)];
    } catch {
      return null;
    }
  }));
  // Ignore results if the user already switched collections.
  if (activeCollectionFilePath() !== collectionPath) {
    return false;
  }
  loaded.forEach((entry) => {
    if (entry) {
      collectionMemberDocs.set(entry[0], entry[1]);
    }
  });
  return true;
}

function missingEnvDependencies(applySession) {
  let env = getRawMergedEnvironment();
  if (applySession) {
    env = applyEncodedCompanions(expandEnvironmentValues(applySessionEnvOverrides(env)));
  }
  const refs = collectEnvRefs(envScanSteps());
  collectEnvRefs(getRawMergedEnvironment(), refs);
  const present = applySession ? isConcreteEnvValue : isAssignedEnvValue;
  return [...refs].filter((path) => {
    let resolved = lookupEnvPath(env, path);
    if (resolved == null && Object.prototype.hasOwnProperty.call(env, path)) {
      resolved = env[path];
    }
    if (CONNECTION_ENV_KEYS.has(path)) {
      return !isConcreteEnvValue(resolved) || isForbiddenApiTarget(resolved);
    }
    return !present(resolved);
  }).sort();
}

function isNonHttpStep(step) {
  const method = String(step?.method || 'GET').toUpperCase();
  return method === 'PREPARE' || method === 'SLEEP' || method === 'EXEC';
}

function stepsNeedSharedBaseUrl(steps) {
  const list = Array.isArray(steps) ? steps : envScanSteps();
  return list.some((step) => {
    if (isNonHttpStep(step)) {
      return false;
    }
    const path = expandPlaceholderDefaults(String(step?.path || step?.url || '').trim());
    return !isAbsoluteHttpUrl(path);
  });
}

function connectionKeysNeedingValues(env) {
  const refs = collectEnvRefs(envScanSteps());
  const scenarioBase = (currentScenario?.base_url || '').trim();
  const needsSharedHost = stepsNeedSharedBaseUrl();
  return [...CONNECTION_ENV_KEYS].filter((key) => {
    const exists = Object.prototype.hasOwnProperty.call(env, key);
    const referenced = refs.has(key);
    const fromScenarioBase = key === 'base_url' && Boolean(scenarioBase);
    if (!referenced && !fromScenarioBase && !(exists && needsSharedHost)) {
      return false;
    }
    const value = exists
      ? env[key]
      : (fromScenarioBase ? scenarioBase : undefined);
    if (!isAssignedEnvValue(value)) {
      return true;
    }
    if (typeof value === 'string' && valueHasEnvPlaceholders(value)) {
      return true;
    }
    return isForbiddenApiTarget(value);
  });
}

function loopbackConnectionKeys(applySession) {
  let env = getRawMergedEnvironment();
  if (applySession) {
    env = applyEncodedCompanions(expandEnvironmentValues(applySessionEnvOverrides(env)));
  }
  return connectionKeysNeedingValues(env);
}

function envScanSources() {
  const sources = [envScanSteps()];
  const env = getRawMergedEnvironment();
  if (env && typeof env === 'object') {
    sources.push(env);
  }
  return sources;
}

function collectAllEnvRefSpecs() {
  const found = new Map();
  envScanSources().forEach((source) => collectEnvRefSpecs(source, found));
  return found;
}

function stepEnvDefaults() {
  return collectAllEnvRefSpecs();
}

function defaultedEnvironmentNames() {
  return [...collectAllEnvRefSpecs().entries()]
    .filter(([, defaultValue]) => defaultValue != null)
    .map(([path]) => path)
    .sort();
}

function fileEnvValue(name) {
  const fileValue = getRawMergedEnvironment()[name] ?? (name === 'base_url' ? currentScenario?.base_url : undefined);
  if (fileValue == null) {
    return '';
  }
  if (typeof fileValue === 'string') {
    const text = fileValue.trim();
    if (!text || valueHasEnvPlaceholders(text)) {
      return '';
    }
    return text;
  }
  if (typeof fileValue === 'object') {
    return '';
  }
  return String(fileValue);
}

function hasTypedEnvValue(value) {
  if (value == null) {
    return false;
  }
  if (typeof value === 'string') {
    return Boolean(value.trim());
  }
  return true;
}

function effectiveEnvFieldValue(name, defaults = collectAllEnvRefSpecs(), session = getSessionEnvOverrides()) {
  if (hasTypedEnvValue(session[name])) {
    return String(session[name]).trim();
  }
  const fromFile = fileEnvValue(name);
  if (fromFile) {
    return fromFile;
  }
  const defaultValue = defaults.get(name);
  return defaultValue != null ? String(defaultValue) : '';
}

function requiredEnvironmentNames() {
  return [...new Set([
    ...missingEnvDependencies(false),
    ...loopbackConnectionKeys(false),
  ])].sort();
}

function displayedEnvironmentNames() {
  return [...new Set([
    ...requiredEnvironmentNames(),
    ...defaultedEnvironmentNames(),
  ])].sort();
}

function unsatisfiedEnvironmentNames() {
  return [...new Set([
    ...missingEnvDependencies(true),
    ...loopbackConnectionKeys(true),
  ])].sort();
}

function environmentOverridesPayload() {
  const overrides = {};
  Object.entries(getSessionEnvOverrides()).forEach(([key, value]) => {
    if (String(value || '').trim()) {
      overrides[key] = String(value).trim();
    }
  });
  return overrides;
}

let lastRequiredEnvNames = '';
let runInProgress = false;

function renderRequiredEnvPanel() {
  const wrap = document.getElementById('session-env-wrap');
  const fields = document.getElementById('session-env-fields');
  const hint = document.getElementById('session-env-run-hint');
  const empty = document.getElementById('session-env-empty');
  if (!wrap || !fields) {
    return;
  }
  syncCurrentScenarioIntoMemberDocs();
  const names = displayedEnvironmentNames();
  const hasOpenWork = Boolean(currentScenario) || Boolean(activeCollectionPath) || names.length > 0;
  if (!hasOpenWork) {
    wrap.classList.add('hidden');
    fields.replaceChildren();
    lastRequiredEnvNames = '';
    return;
  }
  const unsatisfied = unsatisfiedEnvironmentNames();
  const defaults = stepEnvDefaults();
  const session = getSessionEnvOverrides();
  wrap.classList.toggle('hidden', names.length === 0 && unsatisfied.length === 0);
  if (names.length === 0 && unsatisfied.length === 0) {
    fields.replaceChildren();
    lastRequiredEnvNames = '';
    return;
  }
  wrap.classList.remove('hidden');
  wrap.classList.toggle('is-unsatisfied', unsatisfied.length > 0);
  if (hint) {
    hint.classList.toggle('hidden', unsatisfied.length === 0);
  }
  if (empty) {
    empty.classList.toggle('hidden', names.length > 0);
  }
  const signature = names.join('\0');
  if (signature === lastRequiredEnvNames && fields.childElementCount === names.length) {
    names.forEach((name) => {
      const input = fields.querySelector(`[data-env-name="${name.replace(/"/g, '')}"]`);
      if (input && document.activeElement !== input) {
        input.value = effectiveEnvFieldValue(name, defaults, session);
      }
      const hintEl = input?.parentElement?.querySelector('.env-current-value');
      if (hintEl) {
        hintEl.textContent = envFieldSourceHint(name, defaults, session);
      }
    });
    return;
  }
  lastRequiredEnvNames = signature;
  fields.replaceChildren();
  names.forEach((name) => {
    const label = document.createElement('label');
    label.append(document.createTextNode(name));
    const input = document.createElement('input');
    input.type = 'text';
    input.dataset.envName = name;
    const currentValue = effectiveEnvFieldValue(name, defaults, session);
    input.value = currentValue;
    input.autocomplete = 'off';
    input.spellcheck = false;
    const defaultValue = defaults.get(name);
    input.placeholder = defaultValue != null
      ? defaultValue
      : (CONNECTION_ENV_KEYS.has(name) ? 'http://192.168.1.10:8080' : `Value for ${name}`);
    input.addEventListener('input', () => {
      setSessionEnvOverride(name, input.value);
      updateRunAvailability();
      lastHydratedCurlKey = '';
      scheduleStepCurlPreview();
    });
    label.appendChild(input);
    const hintText = envFieldSourceHint(name, defaults, session);
    if (hintText) {
      const current = document.createElement('span');
      current.className = 'muted env-current-value';
      current.textContent = hintText;
      label.appendChild(current);
    }
    fields.appendChild(label);
  });
}

function envFieldSourceHint(name, defaults, session) {
  if (String(session[name] || '').trim()) {
    return 'Override — clear the field to revert.';
  }
  if (fileEnvValue(name)) {
    return 'From the selected environment.';
  }
  if (defaults.get(name) != null) {
    return 'Default — overwrite to change.';
  }
  return '';
}

async function refreshEnvPanelFromCollectionMembers() {
  const before = displayedEnvironmentNames().join('\0');
  const loaded = await ensureCollectionMemberDocs();
  const after = displayedEnvironmentNames().join('\0');
  if (loaded || before !== after) {
    lastRequiredEnvNames = '';
    renderRequiredEnvPanel();
    const unsatisfied = unsatisfiedEnvironmentNames();
    const target = getRunTarget();
    const hostBlocked = Boolean(target.host) && isForbiddenApiHost(target.host);
    const blocked = runInProgress || unsatisfied.length > 0 || hostBlocked;
    if (regressionRunButton) {
      regressionRunButton.disabled = blocked;
      if (unsatisfied.length) {
        regressionRunButton.title = `Set required values: ${unsatisfied.join(', ')}`;
      } else if (hostBlocked) {
        regressionRunButton.title = ROUTABLE_HOST_HELP;
      } else {
        regressionRunButton.title = 'Run Tests';
      }
    }
  }
}

function updateRunAvailability() {
  renderRequiredEnvPanel();
  const unsatisfied = unsatisfiedEnvironmentNames();
  const target = getRunTarget();
  const hostBlocked = Boolean(target.host) && isForbiddenApiHost(target.host);
  const blocked = runInProgress || unsatisfied.length > 0 || hostBlocked;
  if (regressionRunButton) {
    regressionRunButton.disabled = blocked;
    if (unsatisfied.length) {
      regressionRunButton.title = `Set required values: ${unsatisfied.join(', ')}`;
    } else if (hostBlocked) {
      regressionRunButton.title = ROUTABLE_HOST_HELP;
    } else {
      regressionRunButton.title = 'Run Tests';
    }
  }
  if (collectionMemberDocsLoading) {
    return;
  }
  collectionMemberDocsLoading = refreshEnvPanelFromCollectionMembers().finally(() => {
    collectionMemberDocsLoading = null;
  });
}

function getSelectedEnvironmentValues() {
  let merged = applySessionEnvOverrides(getRawMergedEnvironment());
  merged = expandEnvironmentValues(merged, CONNECTION_ENV_KEYS);
  const resolvedHost = merged.server || merged.base_url || merged.baseUrl || merged.url
    || (currentScenario?.base_url || '').trim();
  if (resolvedHost) {
    merged.server = merged.server || resolvedHost;
    if (!merged.base_url) {
      merged.base_url = resolvedHost;
    }
  }
  return applyEncodedCompanions(expandEnvironmentValues(merged));
}

function applyEncodedCompanions(env) {
  const next = {...env};
  Object.keys(next).forEach((key) => {
    if (key.endsWith('_encoded')) {
      return;
    }
    const encodedKey = `${key}_encoded`;
    if (!Object.prototype.hasOwnProperty.call(next, encodedKey)) {
      return;
    }
    const value = next[key];
    if (typeof value !== 'string' || !value.trim() || valueHasEnvPlaceholders(value)) {
      return;
    }
    next[encodedKey] = encodeURIComponent(value);
  });
  return next;
}

function environmentValuesForDisplay() {
  return getSelectedEnvironmentValues();
}

function lookupEnvPath(env, token) {
  if (!token || !env || typeof env !== 'object') {
    return undefined;
  }
  return token.split('.').reduce((current, part) => {
    if (current == null || typeof current !== 'object') {
      return undefined;
    }
    return current[part];
  }, env);
}

function expandEnvPlaceholders(value) {
  if (value == null) {
    return '';
  }
  if (typeof value !== 'string') {
    return expandEnvPlaceholders(JSON.stringify(value));
  }
  const env = environmentValuesForDisplay();
  return value.replace(/\{\{\s*([^}]+)\s*\}\}/g, (match, rawToken) => {
    const {name, defaultValue} = parsePlaceholderToken(rawToken);
    let path = name;
    if (name.startsWith('env.')) {
      path = name.slice(4);
    } else if (name.startsWith('_.')) {
      path = name.slice(2);
    } else if (name === 'meta.environment') {
      const envName = getSelectedEnvironmentName();
      if (envName) {
        return envName;
      }
      return defaultValue != null ? defaultValue : match;
    } else if (name.startsWith('vars.') || name.startsWith('random.') || name.startsWith('meta.')) {
      return defaultValue != null ? defaultValue : match;
    }
    let resolved = lookupEnvPath(env, path);
    if (resolved == null && Object.prototype.hasOwnProperty.call(env, name)) {
      resolved = env[name];
    }
    if (resolved == null || resolved === '') {
      return defaultValue != null ? defaultValue : match;
    }
    return String(resolved);
  });
}

function stepDisplaySummary(step) {
  const method = String(step?.method || 'GET').toUpperCase();
  if (method === 'PREPARE') {
    return 'PREPARE';
  }
  if (method === 'SLEEP') {
    return `SLEEP ${step.seconds ?? 1}s`;
  }
  if (method === 'EXEC') {
    return expandEnvPlaceholders(step.command || step.exec || '');
  }
  return `${method} ${expandEnvPlaceholders(step.path || step.url || '/')}`;
}

function getResolvedBaseUrl() {
  const envValues = getSelectedEnvironmentValues();
  const candidate = envValues.server || envValues.base_url || envValues.baseUrl || envValues.url;
  if (typeof candidate === 'string' && candidate.trim()) {
    return candidate.trim();
  }
  return (currentScenario?.base_url || scenarioBaseUrl.value || '').trim();
}

function parseRunUrl(rawUrl) {
  try {
    const parsed = new URL(rawUrl);
    const scheme = parsed.protocol.replace(':', '') || 'http';
    const port = parsed.port
      ? Number(parsed.port)
      : (scheme === 'https' ? 443 : 80);
    return {scheme, host: parsed.hostname, port};
  } catch {
    return null;
  }
}

function viewingCollection() {
  return isCollectionDocument(currentScenario);
}

function stepAbsoluteOrigin(step) {
  const raw = String(step?.path || step?.url || '').trim();
  if (!isAbsoluteHttpUrl(raw)) {
    return '';
  }
  try {
    return new URL(expandEnvPlaceholders(raw)).origin;
  } catch {
    return '';
  }
}

function requestBaseUrl() {
  const resolved = getResolvedBaseUrl();
  const raw = resolved.includes('://') ? resolved : (resolved ? `http://${resolved}` : '');
  const parsed = raw ? parseRunUrl(raw) : null;
  if (parsed?.host) {
    const implicit = (parsed.scheme === 'https' && parsed.port === 443) || (parsed.scheme === 'http' && parsed.port === 80);
    return implicit ? `${parsed.scheme}://${parsed.host}` : `${parsed.scheme}://${parsed.host}:${parsed.port}`;
  }
  const step = getSelectedStep() || (currentScenario?.steps || [])[0];
  return stepAbsoluteOrigin(step);
}

function getRunTarget() {
  const fromUrl = requestBaseUrl();
  const fromScenario = parseRunUrl(fromUrl || '');
  return {
    scheme: fromScenario?.scheme || 'http',
    host: fromScenario?.host || '',
    port: fromScenario?.port || (fromScenario?.scheme === 'https' ? 443 : 80),
  };
}

function updateRunTargetHint() {
  if (!runTargetHint) {
    return;
  }
  const target = getRunTarget();
  if (!target.host) {
    runTargetHint.textContent = stepsNeedSharedBaseUrl()
      ? 'Select an environment with a server URL, or put a full URL on each step.'
      : 'No environment selected. That is fine until a step needs {{env}} values or a relative path.';
    return;
  }
  if (isForbiddenApiHost(target.host)) {
    runTargetHint.textContent = ROUTABLE_HOST_HELP;
    return;
  }
  const url = requestBaseUrl();
  const envName = getSelectedEnvironmentName();
  if (viewingCollection() && envName) {
    runTargetHint.textContent = `Using “${envName}” from the collection editor → ${url}`;
    return;
  }
  if (envName) {
    runTargetHint.textContent = `Requests go to ${url} using “${envName}”.`;
    return;
  }
  runTargetHint.textContent = `Requests go to ${url}.`;
}

function renderScenarioEnvironmentSelector() {
  const environments = getScenarioEnvironments();
  const names = Object.keys(environments);
  const selectedName = getSelectedEnvironmentName();

  scenarioEnvironmentSelect.innerHTML = '';

  const noneOption = document.createElement('option');
  noneOption.value = '';
  noneOption.textContent = names.length > 0 ? 'No environment override' : 'No environments available';
  scenarioEnvironmentSelect.appendChild(noneOption);

  names.forEach((name) => {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    scenarioEnvironmentSelect.appendChild(option);
  });

  scenarioEnvironmentSelect.value = selectedName;
  syncCollectionRunFields();
  updateRunTargetHint();
}

function syncCollectionRunFields() {
  const hasEnvironments = Object.keys(getScenarioEnvironments()).length > 0;
  if (scenarioBaseUrlWrap) {
    scenarioBaseUrlWrap.classList.toggle('hidden', hasEnvironments);
  }
}

function stringifyJson(value) {
  if (value === undefined) {
    return '';
  }
  return JSON.stringify(value, null, 2);
}

function parseOptionalJson(value, fieldName) {
  const trimmed = value.trim();
  if (!trimmed) {
    return undefined;
  }
  try {
    return JSON.parse(trimmed);
  } catch (error) {
    throw new Error(`${fieldName} must contain valid JSON`);
  }
}

function getValidatedScenarioPayload() {
  if (!currentScenario) {
    currentScenario = createEmptyScenario();
  }

  // Commit pending field edits bound to blur/change handlers.
  if (document.activeElement && typeof document.activeElement.blur === 'function') {
    document.activeElement.blur();
  }

  let payload;
  try {
    payload = JSON.parse(JSON.stringify(currentScenario));
  } catch {
    throw new Error('Scenario contains non-serializable values. Please review JSON fields.');
  }

  if (!Array.isArray(payload.steps)) {
    throw new Error('Scenario JSON is invalid: steps must be an array.');
  }
  const collectionFile = Array.isArray(payload.scenarios) && payload.scenarios.length > 0 && payload.steps.length === 0;
  if (payload.environments != null && (typeof payload.environments !== 'object' || Array.isArray(payload.environments))) {
    throw new Error('Scenario JSON is invalid: environments must be a JSON object.');
  }
  if (payload.random_generators != null && (typeof payload.random_generators !== 'object' || Array.isArray(payload.random_generators))) {
    throw new Error('Scenario JSON is invalid: random_generators must be a JSON object.');
  }
  if (!collectionFile) {
    if (!payload.environments || Object.keys(payload.environments).length === 0) {
      delete payload.environments;
    }
    if (!payload.random_generators || Object.keys(payload.random_generators).length === 0) {
      delete payload.random_generators;
    }
    if (!String(payload.base_url || '').trim()) {
      delete payload.base_url;
    }
  }

  return payload;
}

function jsonValueIsSet(value) {
  if (value === undefined || value === null) {
    return false;
  }
  if (typeof value === 'string') {
    return value.trim() !== '';
  }
  if (Array.isArray(value)) {
    return value.length > 0;
  }
  if (typeof value === 'object') {
    return Object.keys(value).length > 0;
  }
  return true;
}

function syncJsonFieldButton(button, value) {
  if (!button) {
    return;
  }
  const set = jsonValueIsSet(value);
  const label = button.dataset.label || 'JSON';
  button.classList.toggle('is-set', set);
  button.dataset.set = set ? 'true' : 'false';
  button.setAttribute('aria-label', set ? `Edit ${label}, values set` : `Edit ${label}`);
  button.title = set ? `${label} — values set` : `Edit ${label}`;
  const flag = button.querySelector('.json-field-flag');
  if (flag) {
    flag.hidden = !set;
  }
}

function formatExpectedStatus(value) {
  if (value === undefined) {
    return '';
  }
  if (Array.isArray(value)) {
    return value.join(',');
  }
  return String(value);
}

function parseExpectedStatus(value) {
  const trimmed = value.trim();
  if (!trimmed) {
    return undefined;
  }
  const parts = trimmed.split(',').map((item) => Number(item.trim())).filter((item) => !Number.isNaN(item));
  if (parts.length === 0) {
    return undefined;
  }
  return parts.length === 1 ? parts[0] : parts;
}

function getSelectedStep() {
  if (!currentScenario || selectedStepIndex < 0 || selectedStepIndex >= currentScenario.steps.length) {
    return null;
  }
  return currentScenario.steps[selectedStepIndex];
}

function syncScenarioPreview() {
  if (!currentScenario) {
    scenarioEditor.value = '';
    return;
  }
  scenarioEditor.value = JSON.stringify(currentScenario, null, 2);
}

function renderStepSequence() {
  stepSequence.innerHTML = '';
  const steps = currentScenario?.steps || [];
  stepCount.textContent = `${steps.length} step${steps.length === 1 ? '' : 's'}`;
  if (testSequenceButton) {
    testSequenceButton.disabled = steps.length === 0;
  }

  steps.forEach((step, index) => {
    const card = document.createElement('div');
    card.className = `step-card${index === selectedStepIndex ? ' selected' : ''}`;
    card.draggable = true;

    card.addEventListener('dragstart', (event) => {
      draggedStepIndex = index;
      card.classList.add('dragging');
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', String(index));
    });

    card.addEventListener('dragend', () => {
      draggedStepIndex = -1;
      stepSequence.querySelectorAll('.step-card.drag-over').forEach((item) => {
        item.classList.remove('drag-over');
      });
      stepSequence.querySelectorAll('.step-card.dragging').forEach((item) => {
        item.classList.remove('dragging');
      });
    });

    card.addEventListener('dragover', (event) => {
      if (draggedStepIndex < 0 || draggedStepIndex === index) {
        return;
      }
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      card.classList.add('drag-over');
    });

    card.addEventListener('dragleave', () => {
      card.classList.remove('drag-over');
    });

    card.addEventListener('drop', (event) => {
      event.preventDefault();
      card.classList.remove('drag-over');
      if (draggedStepIndex < 0 || draggedStepIndex === index) {
        return;
      }
      // Drop places the dragged step before the target tile.
      const targetIndex = draggedStepIndex < index ? index - 1 : index;
      moveStep(draggedStepIndex, targetIndex);
    });

    const body = document.createElement('button');
    body.className = 'step-card-body';
    body.type = 'button';
    body.innerHTML = `
      <span class="step-index">${index + 1}</span>
      <div class="step-copy">
        <strong>${escapeHtml(step.name || `step_${index + 1}`)}</strong>
        <span>${escapeHtml(stepDisplaySummary(step))}</span>
      </div>
    `;
    body.addEventListener('click', () => {
      selectedStepIndex = index;
      renderScenarioBuilder();
    });
    card.append(body);
    stepSequence.appendChild(card);
  });
}

function renderTestStepResult(result) {
  stepTestOutput.textContent = typeof result === 'string' ? result : JSON.stringify(result, null, 2);
}

function renderStepDetail() {
  const step = getSelectedStep();
  if (!step) {
    stepDetailEmpty.classList.remove('hidden');
    stepDetailForm.classList.add('hidden');
    selectedStepLabel.textContent = 'none';
    stepMoveUpButton.disabled = true;
    stepMoveDownButton.disabled = true;
    stepDeleteButton.disabled = true;
    testStepButton.disabled = true;
    renderTestStepResult('No step selected.');
    renderStepCurl('Select a step to see curl.');
    return;
  }

  stepDetailEmpty.classList.add('hidden');
  stepDetailForm.classList.remove('hidden');
  selectedStepLabel.textContent = step.name || `step_${selectedStepIndex + 1}`;
  stepMoveUpButton.disabled = selectedStepIndex === 0;
  stepMoveDownButton.disabled = selectedStepIndex === currentScenario.steps.length - 1;
  stepDeleteButton.disabled = false;
  testStepButton.disabled = false;

  stepNameInput.value = step.name || '';
  ensureMethodOption(step.method || 'GET');
  stepMethodInput.value = (step.method || 'GET').toUpperCase();
  stepPathInput.value = step.path || step.url || '';
  if (stepPathResolved) {
    const raw = step.path || step.url || '';
    const resolved = expandEnvPlaceholders(raw);
    if (isAbsoluteHttpUrl(raw)) {
      stepPathResolved.textContent = resolved !== raw
        ? resolved
        : 'Full URL — the collection base URL is not applied.';
    } else {
      stepPathResolved.textContent = resolved && resolved !== raw ? resolved : '';
    }
  }
  stepTimeoutInput.value = step.timeout ?? '';
  stepExpectedStatusInput.value = formatExpectedStatus(step.expected_status);
  stepSaveResponseAsInput.value = step.save_response_as || '';
  syncJsonFieldButton(editStepHeadersJsonButton, step.headers);
  syncJsonFieldButton(editStepJsonButton, step.json);
  syncJsonFieldButton(editStepSaveJsonButton, step.save);
  syncJsonFieldButton(editStepExpectedJsonButton, step.expected_json_contains);
  if (stepFollowRedirectsInput) {
    stepFollowRedirectsInput.checked = Boolean(step.follow_redirects);
  }
  stepStopOnFailureInput.checked = Boolean(step.stop_on_failure);
  scheduleStepCurlPreview();
}

function renderScenarioBuilder() {
  if (!currentScenario) {
    currentScenario = createEmptyScenario();
  }
  scenarioBaseUrl.value = currentScenario.base_url || '';
  renderScenarioEnvironmentSelector();
  renderHierarchy();
  renderStepSequence();
  renderStepDetail();
  syncScenarioPreview();
}

function getJsonDialogConfig(target) {
  if (target === 'environments') {
    const editingCollectionEnv = Boolean(isCollectionDocument(currentScenario) || (activeCollectionDocument && Object.keys(getCollectionEnvironments()).length > 0));
    return {
      title: editingCollectionEnv ? 'Edit Collection Environments JSON' : 'Edit Environments JSON',
      fieldName: 'Environments',
      currentValue: () => getScenarioEnvironments(),
      apply: (parsed) => {
        const previous = getScenarioEnvironments();
        const next = parsed || {};
        syncRemovedEnvKeys(previous, next);
        if (isCollectionDocument(currentScenario) || !activeCollectionDocument) {
          currentScenario.environments = next;
        } else {
          activeCollectionDocument.environments = next;
        }
        const names = Object.keys(next);
        if (!names.includes(currentScenario.selected_environment || '')) {
          currentScenario.selected_environment = '';
        }
        if (activeCollectionDocument && names.includes(currentScenario.selected_environment || '')) {
          activeCollectionDocument.selected_environment = currentScenario.selected_environment;
        }
      },
    };
  }
  if (target === 'random_generators') {
    const editingCollection = Boolean(isCollectionDocument(currentScenario) || activeCollectionDocument);
    return {
      title: editingCollection ? 'Edit Collection Constants JSON' : 'Edit Constants JSON',
      fieldName: 'Constants',
      currentValue: () => {
        if (isCollectionDocument(currentScenario) || !activeCollectionDocument) {
          return currentScenario?.random_generators || {};
        }
        return activeCollectionDocument.random_generators || {};
      },
      apply: (parsed) => {
        const next = parsed || {};
        if (isCollectionDocument(currentScenario) || !activeCollectionDocument) {
          currentScenario.random_generators = next;
        } else {
          activeCollectionDocument.random_generators = next;
        }
      },
    };
  }
  if (target === 'step_headers') {
    const step = getSelectedStep();
    if (!step) throw new Error('Select a step first.');
    return {
      title: 'Edit Step Headers JSON',
      fieldName: 'Headers',
      currentValue: () => step.headers || {},
      apply: (parsed) => {
        if (parsed === undefined) {
          delete step.headers;
          return;
        }
        step.headers = parsed;
      },
    };
  }
  if (target === 'step_json') {
    const step = getSelectedStep();
    if (!step) throw new Error('Select a step first.');
    return {
      title: 'Edit Step Request Body JSON',
      fieldName: 'Request JSON body',
      currentValue: () => step.json,
      apply: (parsed) => {
        if (parsed === undefined) {
          delete step.json;
          return;
        }
        step.json = parsed;
      },
    };
  }
  if (target === 'step_save') {
    const step = getSelectedStep();
    if (!step) throw new Error('Select a step first.');
    return {
      title: 'Edit Step Save Mapping JSON',
      fieldName: 'Save mapping',
      currentValue: () => step.save || {},
      apply: (parsed) => {
        if (parsed === undefined) {
          delete step.save;
          return;
        }
        step.save = parsed;
      },
    };
  }
  if (target === 'step_expected_json') {
    const step = getSelectedStep();
    if (!step) throw new Error('Select a step first.');
    return {
      title: 'Edit Step Expected JSON',
      fieldName: 'Expected JSON contains',
      currentValue: () => step.expected_json_contains,
      apply: (parsed) => {
        if (parsed === undefined) {
          delete step.expected_json_contains;
          return;
        }
        step.expected_json_contains = parsed;
      },
    };
  }
  throw new Error('Unknown JSON dialog target');
}

function closeJsonDialog() {
  currentJsonDialogTarget = null;
  jsonDialog.classList.add('hidden');
  jsonDialogEditor.setCustomValidity('');
}

async function clonePathToWorkspace(path) {
  if (!path) {
    throw new Error('Open a collection first');
  }
  if (isWorkspacePath(path)) {
    return {path, id: workspaceCollectionId(path)};
  }
  if (!authState.authenticated) {
    throw new Error(requireSignInMessage());
  }
  return api('/api/workspace/clone', {
    method: 'POST',
    body: JSON.stringify({path}),
  });
}

async function ensureWorkspaceCollectionId() {
  if (isWorkspacePath(activeCollectionPath)) {
    return workspaceCollectionId(activeCollectionPath);
  }
  if (activeCollectionPath) {
    const cloned = await clonePathToWorkspace(activeCollectionPath);
    activeCollectionPath = cloned.path;
    return cloned.id;
  }
  if (!authState.authenticated) {
    throw new Error(requireSignInMessage());
  }
  const created = await api('/api/workspace/collections', {
    method: 'POST',
    body: JSON.stringify({name: 'Untitled'}),
  });
  activeCollectionPath = created.path;
  return created.id;
}

async function saveScenarioIfNamed() {
  const name = scenarioNameInput.value.trim();
  if (!name || !currentScenario) {
    return;
  }
  if (isCollectionDocument(currentScenario)) {
    return;
  }
  const collectionId = await ensureWorkspaceCollectionId();
  const filename = name.split('/').pop() || name;
  const path = workspaceFilePath(collectionId, filename);
  const payload = getValidatedScenarioPayload();
  const result = await api(scenarioFileUrl(path), {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  currentScenario = payload;
  selectedScenarioName = result.path || path;
  scenarioNameInput.value = selectedScenarioName;
}

async function persistActiveCollectionDocument() {
  if (!activeCollectionDocument) {
    return;
  }
  const collectionId = await ensureWorkspaceCollectionId();
  const path = workspaceFilePath(collectionId, collectionDocumentFilename(activeCollectionPath));
  const collection = await api(scenarioFileUrl(path));
  if (!collection || typeof collection !== 'object') {
    return;
  }
  collection.environments = activeCollectionDocument.environments || {};
  collection.random_generators = activeCollectionDocument.random_generators || {};
  if (activeCollectionDocument.selected_environment) {
    collection.selected_environment = activeCollectionDocument.selected_environment;
  }
  if (activeCollectionDocument.description != null) {
    collection.description = activeCollectionDocument.description;
  }
  await api(scenarioFileUrl(path), {
    method: 'POST',
    body: JSON.stringify(collection),
  });
  activeCollectionPath = path;
}

function openJsonDialog(target) {
  if (!currentScenario) {
    currentScenario = createEmptyScenario();
  }
  let config;
  try {
    config = getJsonDialogConfig(target);
  } catch (error) {
    alert(error.message || String(error));
    return;
  }
  currentJsonDialogTarget = target;
  jsonDialogTitle.textContent = config.title;
  jsonDialogEditor.value = stringifyJson(config.currentValue());
  jsonDialog.classList.remove('hidden');
  jsonDialogEditor.focus();
}

async function saveJsonDialog() {
  if (!currentJsonDialogTarget) {
    return;
  }
  if (!currentScenario) {
    currentScenario = createEmptyScenario();
  }

  const config = getJsonDialogConfig(currentJsonDialogTarget);
  try {
    const parsed = parseOptionalJson(jsonDialogEditor.value, config.fieldName);
    config.apply(parsed);
    if (currentJsonDialogTarget === 'environments' || currentJsonDialogTarget === 'random_generators') {
      await persistActiveCollectionDocument();
    }
    await saveScenarioIfNamed();
    closeJsonDialog();
    renderScenarioBuilder();
    runOutput.textContent = `${config.fieldName} saved to scenario.`;
  } catch (error) {
    jsonDialogEditor.setCustomValidity(error.message);
    jsonDialogEditor.reportValidity();
  }
}

function moveStep(fromIndex, toIndex) {
  if (!currentScenario || toIndex < 0 || toIndex >= currentScenario.steps.length) {
    return;
  }
  const [step] = currentScenario.steps.splice(fromIndex, 1);
  currentScenario.steps.splice(toIndex, 0, step);
  selectedStepIndex = toIndex;
  renderScenarioBuilder();
}

function deleteStep(index) {
  if (!currentScenario) {
    return;
  }
  currentScenario.steps.splice(index, 1);
  if (currentScenario.steps.length === 0) {
    selectedStepIndex = -1;
  } else if (selectedStepIndex >= currentScenario.steps.length) {
    selectedStepIndex = currentScenario.steps.length - 1;
  }
  renderScenarioBuilder();
}

function addStep() {
  if (!currentScenario) {
    currentScenario = createEmptyScenario();
  }
  currentScenario.steps.push(createEmptyStep());
  selectedStepIndex = currentScenario.steps.length - 1;
  renderScenarioBuilder();
}

function isAbsoluteHttpUrl(value) {
  return /^https?:\/\//i.test(String(value || '').trim());
}

function ensureMethodOption(method) {
  const value = String(method || 'GET').toUpperCase() || 'GET';
  if (stepMethodInput && ![...stepMethodInput.options].some((option) => option.value === value)) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value;
    stepMethodInput.appendChild(option);
  }
  return value;
}

function openCurlDialog() {
  if (!getSelectedStep() || !curlDialog) {
    alert('Select a step first');
    return;
  }
  if (curlDialogEditor) {
    curlDialogEditor.value = '';
  }
  curlDialog.classList.remove('hidden');
  window.setTimeout(() => curlDialogEditor?.focus(), 0);
}

function closeCurlDialog() {
  curlDialog?.classList.add('hidden');
}

function applyParsedCurl(parsed) {
  const step = getSelectedStep();
  if (!step || !parsed) {
    return;
  }
  const method = ensureMethodOption(parsed.method || 'GET');
  step.method = method;
  const path = String(parsed.path || parsed.url || '').trim();
  if (path) {
    step.path = path;
    delete step.url;
  }
  if (parsed.headers && typeof parsed.headers === 'object' && Object.keys(parsed.headers).length) {
    step.headers = parsed.headers;
  } else {
    delete step.headers;
  }
  if (Object.prototype.hasOwnProperty.call(parsed, 'json')) {
    step.json = parsed.json;
    delete step.data;
  } else if (parsed.data !== undefined && parsed.data !== null && parsed.data !== '') {
    step.data = parsed.data;
    delete step.json;
  } else {
    delete step.json;
    delete step.data;
  }
  if (parsed.timeout) {
    step.timeout = parsed.timeout;
  }
  if (parsed.follow_redirects) {
    step.follow_redirects = true;
  } else {
    delete step.follow_redirects;
  }
  if ((!step.name || step.name === 'new_step') && parsed.name) {
    step.name = parsed.name;
  }
  lastHydratedCurlKey = '';
  renderScenarioBuilder();
}

async function applyCurlDialog() {
  const text = (curlDialogEditor?.value || '').trim();
  if (!text) {
    alert('Paste a curl command');
    return;
  }
  try {
    const parsed = await api('/api/parse-curl', {
      method: 'POST',
      body: JSON.stringify({curl: text}),
    });
    applyParsedCurl(parsed);
    closeCurlDialog();
    if (runOutput) {
      runOutput.textContent = `Imported curl into ${getSelectedStep()?.name || 'the selected step'}.`;
    }
  } catch (error) {
    alert(error.message || String(error));
  }
}

function bindStepField(element, updater) {
  element.addEventListener('input', () => {
    const step = getSelectedStep();
    if (!step) {
      return;
    }
    try {
      updater(step, element);
      renderScenarioBuilder();
    } catch (error) {
      element.setCustomValidity(error.message);
      element.reportValidity();
    }
  });
  element.addEventListener('change', () => element.setCustomValidity(''));
}

function bindScenarioField(element, updater) {
  element.addEventListener('input', () => {
    if (!currentScenario) {
      currentScenario = createEmptyScenario();
    }
    try {
      updater(currentScenario, element);
      syncScenarioPreview();
    } catch (error) {
      element.setCustomValidity(error.message);
      element.reportValidity();
    }
  });
  element.addEventListener('change', () => element.setCustomValidity(''));
}

// eslint-disable-next-line no-control-regex
const ANSI_RE = /\x1b\[[0-9;]*m/g;
function stripAnsi(str) {
  return str ? str.replace(ANSI_RE, '') : '';
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

function persistLastOpen() {
  try {
    sessionStorage.setItem(LAST_OPEN_STORAGE_KEY, JSON.stringify({
      scenario: selectedScenarioName || '',
      collection: activeCollectionPath || '',
    }));
  } catch {
    // Ignore quota / private-mode failures.
  }
}

function restoreLastOpen() {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(LAST_OPEN_STORAGE_KEY) || '{}');
    return {
      scenario: typeof parsed.scenario === 'string' ? parsed.scenario : '',
      collection: typeof parsed.collection === 'string' ? parsed.collection : (typeof parsed.suite === 'string' ? parsed.suite : ''),
    };
  } catch {
    return {scenario: '', collection: ''};
  }
}

async function reopenScenarioFile(path, options = {}) {
  await openScenario({path, name: path.split('/').pop() || path}, options);
}

async function loadScenarios() {
  scenarioTree = await api('/api/scenarios');
  if (!didExpandAllFolders) {
    collectFolderPaths(scenarioTree.children).forEach((path) => expandedFolders.add(path));
    didExpandAllFolders = true;
  }
  expandedFolders.add('workspace');
  expandedFolders.add('shared');
  renderExplorer();
  let openPath = selectedScenarioName;
  let collectionPath = activeCollectionPath;
  if (!openPath) {
    const last = restoreLastOpen();
    openPath = last.scenario;
    collectionPath = last.collection;
  }
  if (openPath) {
    try {
      if (collectionPath && collectionPath !== openPath) {
        await reopenScenarioFile(collectionPath);
        await reopenScenarioFile(openPath, {fromCollection: true});
        return;
      }
      await reopenScenarioFile(openPath);
      return;
    } catch {
      selectedScenarioName = null;
    }
  }
  const first = findFirstCollectionFile(scenarioTree.children);
  if (first) {
    expandedCollections.add(first.path);
    await openScenario(first);
  }
}

function isCollectionDocument(scenario, path = selectedScenarioName || activeCollectionPath) {
  if (isCollectionFileName(path)) {
    return true;
  }
  return Array.isArray(scenario?.scenarios) && (!Array.isArray(scenario.steps) || scenario.steps.length === 0);
}

function fileLabel(path) {
  const name = String(path || '').split('/').pop() || '';
  return name.replace(/\.json$/i, '') || name;
}

function rememberCollectionContext(path, scenario, fromCollection) {
  if (isCollectionDocument(scenario, path)) {
    if (activeCollectionPath !== path || collectionMemberDocsCollection !== path) {
      clearCollectionMemberDocs(path);
    }
    activeCollectionPath = path;
    activeCollectionMembers = Array.isArray(scenario.scenarios) ? scenario.scenarios.slice() : [];
    captureActiveCollectionDocument(scenario, path);
    return;
  }
  if (fromCollection) {
    return;
  }
  const fileName = String(path || '').split('/').pop();
  const sameFolder = activeCollectionPath && parentFolderPath(path) === parentFolderPath(activeCollectionPath);
  if (!(sameFolder && activeCollectionMembers.includes(fileName))) {
    clearCollectionMemberDocs();
    activeCollectionPath = '';
    activeCollectionMembers = [];
    activeCollectionDocument = null;
  }
}

function hasCollectionContext() {
  return Boolean(activeCollectionPath) || isCollectionDocument(currentScenario);
}

function captureActiveCollectionDocument(scenario, path = selectedScenarioName || activeCollectionPath) {
  if (!isCollectionDocument(scenario, path)) {
    return;
  }
  activeCollectionDocument = {
    name: scenario.name || '',
    environments: scenario.environments || {},
    random_generators: scenario.random_generators || {},
    selected_environment: scenario.selected_environment || '',
    base_url: scenario.base_url || '',
    description: scenario.description || '',
  };
}

function renderHierarchy() {
  const viewingCollection = isCollectionDocument(currentScenario);
  const hasCollection = hasCollectionContext();
  if (editorKind) {
    editorKind.textContent = 'Collection';
    editorKind.className = 'kind-pill kind-collection';
  }
  if (editorTitle) {
    const collectionPath = activeCollectionPath || (viewingCollection ? selectedScenarioName : '');
    editorTitle.textContent = currentScenario?.name || activeCollectionDocument?.name || (collectionPath ? fileLabel(collectionPath) : 'Collection');
  }
  if (copyCollectionButton) {
    const openPath = activeCollectionPath || selectedScenarioName || '';
    const canCopy = Boolean(openPath) && hasCollection && authState.authenticated;
    copyCollectionButton.classList.toggle('hidden', !canCopy || isWorkspacePath(openPath));
    copyCollectionButton.textContent = 'Copy to workspace';
  }
  const collectionPath = activeCollectionPath || (viewingCollection ? selectedScenarioName : '');
  const workspaceCollectionOpen = Boolean(collectionPath) && isWorkspacePath(collectionPath) && authState.authenticated;
  const editable = !workspaceCollectionOpen || canEditActiveCollection();
  const owned = !workspaceCollectionOpen || isOwnerOfActiveCollection();
  if (importCollectionButton) {
    importCollectionButton.classList.toggle('hidden', !workspaceCollectionOpen || !editable);
  }
  if (exportCollectionBrunoButton) {
    exportCollectionBrunoButton.classList.toggle('hidden', !collectionPath || !authState.authenticated);
  }
  if (shareCollectionButton) {
    shareCollectionButton.classList.toggle(
      'hidden',
      !workspaceCollectionOpen || !owned || findTreeNode(collectionPath)?.source === 'shared',
    );
  }
  if (deleteCollectionButton) {
    deleteCollectionButton.classList.toggle('hidden', !collectionPath || !authState.authenticated || !isWorkspacePath(collectionPath) || !owned);
  }
  if (deleteScenarioButton) {
    const scenarioPath = selectedScenarioName || '';
    const showScenarioDelete = Boolean(scenarioPath) && !viewingCollection && authState.authenticated && editable;
    deleteScenarioButton.classList.toggle('hidden', !showScenarioDelete);
  }
  if (saveCollectionButton) {
    saveCollectionButton.disabled = workspaceCollectionOpen && !editable;
    saveCollectionButton.title = workspaceCollectionOpen && !editable ? 'This shared collection is read-only' : '';
  }
  if (runTitle) {
    runTitle.textContent = 'Run';
  }
  if (saveScenarioButton) {
    saveScenarioButton.textContent = 'Save Scenario';
    saveScenarioButton.disabled = workspaceCollectionOpen && !editable;
    saveScenarioButton.title = workspaceCollectionOpen && !editable ? 'This shared collection is read-only' : '';
  }
  if (addStepButton) {
    addStepButton.classList.toggle('hidden', viewingCollection);
    addStepButton.disabled = workspaceCollectionOpen && !editable;
  }
  if (editRandomGeneratorsJsonButton) {
    editRandomGeneratorsJsonButton.textContent = 'Edit Collection Constants';
  }
  if (editEnvironmentsJsonButton) {
    editEnvironmentsJsonButton.textContent = 'Edit Collection Environments';
  }
  if (viewingCollection) {
    captureActiveCollectionDocument(currentScenario);
  }
  syncCollectionRunFields();
  if (collectionSection) {
    collectionSection.classList.toggle('hidden', !hasCollection);
  }
  if (scenarioSection) {
    scenarioSection.classList.toggle('hidden', viewingCollection || !currentScenario);
  }
  if (scenarioBuilder) {
    scenarioBuilder.classList.toggle('hidden', viewingCollection);
  }
  if (collectionPanel) {
    collectionPanel.classList.toggle('hidden', !hasCollection);
  }
  renderBreadcrumb();
  if (hasCollection) {
    renderCollectionMembers();
  }
  updateRunAvailability();
}

function renderBreadcrumb() {
  if (!editorCrumb) {
    return;
  }
  editorCrumb.innerHTML = '';
  const path = selectedScenarioName || scenarioNameInput.value.trim();
  if (!path && !currentScenario) {
    editorCrumb.innerHTML = '<span class="muted">Select a collection first, then a scenario in that collection.</span>';
    return;
  }
  const parts = [];
  const folder = parentFolderPath(path);
  if (folder) {
    parts.push({label: folder.split('/').pop(), title: folder});
  }
  if (activeCollectionPath && !isCollectionDocument(currentScenario)) {
    parts.push({
      label: fileLabel(activeCollectionPath),
      title: 'Collection',
      kind: 'collection',
      action: () => {
        const node = {path: activeCollectionPath, name: activeCollectionPath.split('/').pop()};
        void openScenario(node);
      },
    });
  }
  if (path) {
    parts.push({
      label: fileLabel(path),
      title: isCollectionDocument(currentScenario) ? 'Collection' : 'Scenario',
      kind: isCollectionDocument(currentScenario) ? 'collection' : 'scenario',
      current: true,
    });
  }
  const step = getSelectedStep();
  if (step) {
    parts.push({
      label: `${selectedStepIndex + 1}. ${step.name || `step_${selectedStepIndex + 1}`}`,
      title: 'Step',
      kind: 'step',
      current: true,
    });
  }
  parts.forEach((part, index) => {
    if (index > 0) {
      const sep = document.createElement('span');
      sep.className = 'crumb-sep';
      sep.textContent = '›';
      editorCrumb.appendChild(sep);
    }
    if (part.action) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'crumb';
      button.textContent = part.label;
      button.title = part.title || part.label;
      button.addEventListener('click', part.action);
      editorCrumb.appendChild(button);
    } else {
      const current = document.createElement('span');
      current.className = part.current ? 'crumb-current' : 'crumb';
      current.textContent = part.label;
      current.title = part.title || part.label;
      editorCrumb.appendChild(current);
    }
  });
}

function renderCollectionMembers() {
  if (!collectionMembers) {
    return;
  }
  collectionMembers.innerHTML = '';
  const viewingCollection = isCollectionDocument(currentScenario);
  const members = viewingCollection
    ? (Array.isArray(currentScenario?.scenarios) ? currentScenario.scenarios : [])
    : activeCollectionMembers.slice();
  if (collectionMemberCount) {
    collectionMemberCount.textContent = `${members.length} scenario${members.length === 1 ? '' : 's'}`;
  }
  if (collectionDescription) {
    const text = viewingCollection
      ? (currentScenario?.description || '')
      : (activeCollectionDocument?.description || '');
    collectionDescription.textContent = text || 'This collection runs the listed scenarios in order.';
  }
  const collectionPath = (viewingCollection ? selectedScenarioName : activeCollectionPath) || '';
  const folder = parentFolderPath(collectionPath);
  const canReorder = isWorkspacePath(collectionPath);
  members.forEach((name, index) => {
    const fileName = String(name);
    const memberPath = joinPath(folder, fileName);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `collection-member${memberPath === selectedScenarioName ? ' is-current' : ''}`;
    button.innerHTML = `<span>${index + 1}. ${escapeHtml(fileLabel(fileName))}</span><span class="tree-kind">Scenario</span>`;
    if (canReorder) {
      button.draggable = true;
      button.addEventListener('dragstart', (event) => {
        explorerDrag = {kind: 'scenario', path: memberPath, parent: collectionPath, draggable: true};
        button.classList.add('dragging');
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', memberPath);
      });
      button.addEventListener('dragend', () => {
        explorerDrag = null;
        collectionMembers.querySelectorAll('.collection-member.dragging, .collection-member.drag-over').forEach((item) => {
          item.classList.remove('dragging', 'drag-over');
        });
      });
      button.addEventListener('dragover', (event) => {
        if (!explorerDrag || explorerDrag.path === memberPath || explorerDrag.parent !== collectionPath) {
          return;
        }
        event.preventDefault();
        button.classList.add('drag-over');
      });
      button.addEventListener('dragleave', () => button.classList.remove('drag-over'));
      button.addEventListener('drop', (event) => {
        event.preventDefault();
        button.classList.remove('drag-over');
        if (explorerDrag) {
          void reorderDropped(explorerDrag, {kind: 'scenario', path: memberPath, parent: collectionPath});
        }
      });
    }
    button.addEventListener('click', () => {
      void openScenario({path: memberPath, name: fileName}, {fromCollection: true});
    });
    collectionMembers.appendChild(button);
  });
}

async function openCollectionMember(collectionNode, memberName) {
  if (!collectionNode?.path || !memberName) {
    return;
  }
  expandedCollections.add(collectionNode.path);
  if (activeCollectionPath !== collectionNode.path || !activeCollectionDocument) {
    await openScenario(collectionNode);
  }
  const memberPath = joinPath(parentFolderPath(collectionNode.path), memberName);
  await openScenario({path: memberPath, name: memberName}, {fromCollection: true});
}

async function openScenario(item, options = {}) {
  const raw = await api(scenarioFileUrl(item.path));
  const scenario = applyDocumentAccess(raw);
  selectedScenarioName = item.path;
  const collectionNode = isCollectionNode(item) ? item : findTreeNode(activeCollectionPath);
  selectedFolderPath = collectionNode ? folderPathForCollection(collectionNode) : parentFolderPath(item.path);
  expandAncestorFolders(item.path);
  scenarioNameInput.value = item.path;
  currentScenario = normalizeScenario(scenario);
  if (Array.isArray(scenario?.scenarios)) {
    currentScenario.scenarios = scenario.scenarios;
  }
  lastStepContextVars = {};
  lastHydratedCurlKey = '';
  selectedStepIndex = currentScenario.steps.length > 0 ? 0 : -1;
  rememberCollectionContext(item.path, currentScenario, Boolean(options?.fromCollection));
  if (isCollectionDocument(currentScenario)) {
    expandedCollections.add(item.path);
  } else if (activeCollectionPath) {
    expandedCollections.add(activeCollectionPath);
  }
  if (!isCollectionDocument(currentScenario) && !options.fromCollection) {
    try {
      const inherited = await api(parentCollectionUrl(item.path));
      if (inherited?.status === 'ok' && inherited.environments) {
        activeCollectionPath = inherited.path || '';
        activeCollectionMembers = Array.isArray(inherited.scenarios) ? inherited.scenarios : [];
        activeCollectionDocument = {
          environments: inherited.environments || {},
          random_generators: inherited.random_generators || {},
          selected_environment: inherited.selected_environment || '',
          base_url: inherited.base_url || '',
          description: inherited.description || '',
        };
      }
    } catch {
      // Scenario can still open without a parent collection.
    }
  }
  persistLastOpen();
  if (isWorkspacePath(activeCollectionPath || item.path)) {
    loadedWorkspaceEnvKey = '';
    try {
      await loadWorkspaceEnvValues();
    } catch {
      // Workspace env is optional; missing-values panel still works from session storage.
    }
  }
  renderExplorer();
  renderScenarioBuilder();
}

async function saveCollection() {
  try {
    if (isCollectionDocument(currentScenario)) {
      const sourcePath = activeCollectionPath || selectedScenarioName;
      if (!sourcePath) {
        alert('Open a collection first');
        return;
      }
      const collectionId = isWorkspacePath(sourcePath)
        ? workspaceCollectionId(sourcePath)
        : (await clonePathToWorkspace(sourcePath)).id;
      const path = workspaceFilePath(collectionId, collectionDocumentFilename(sourcePath));
      const parsed = getValidatedScenarioPayload();
      await api(scenarioFileUrl(path), {
        method: 'POST',
        body: JSON.stringify(parsed),
      });
      currentScenario = parsed;
      selectedScenarioName = path;
      activeCollectionPath = path;
      selectedFolderPath = parentFolderPath(path);
      captureActiveCollectionDocument(currentScenario);
      await persistWorkspaceEnvValues();
      await loadScenarios();
      if (runOutput) {
        runOutput.textContent = 'Collection saved to your workspace.';
      }
      return;
    }
    await persistActiveCollectionDocument();
    await persistWorkspaceEnvValues();
    if (runOutput) {
      runOutput.textContent = 'Collection saved to your workspace.';
    }
  } catch (error) {
    alert(error.message || String(error));
  }
}

async function saveScenario() {
  const name = scenarioNameInput.value.trim();
  if (!name) {
    alert('Scenario name is required');
    return;
  }
  try {
    await saveScenarioIfNamed();
    selectedFolderPath = parentFolderPath(selectedScenarioName || name);
    expandAncestorFolders(selectedScenarioName || name);
    await persistWorkspaceEnvValues();
    await loadScenarios();
    renderExplorer();
    runOutput.textContent = 'Scenario saved to your workspace.';
  } catch (error) {
    alert(error.message || String(error));
  }
}

async function copyOpenCollectionToWorkspace() {
  const source = activeCollectionPath || selectedScenarioName;
  const cloned = await clonePathToWorkspace(source);
  expandedFolders.add('workspace');
  expandedCollections.add(cloned.path);
  await loadScenarios();
  await reopenScenarioFile(cloned.path);
  if (runOutput) {
    runOutput.textContent = 'Copied to your workspace.';
  }
}

function resetOpenDocuments() {
  selectedScenarioName = null;
  selectedFolderPath = '';
  activeCollectionPath = '';
  activeCollectionMembers = [];
  activeCollectionDocument = null;
  clearCollectionMemberDocs();
  currentScenario = null;
  selectedStepIndex = -1;
  lastStepContextVars = {};
  lastHydratedCurlKey = '';
  loadedWorkspaceEnvKey = '';
}

async function deletePath(path) {
  return api(`/api/workspace/item?path=${encodeURIComponent(path)}`, {method: 'DELETE'});
}

async function deleteOpenCollection() {
  const path = activeCollectionPath || selectedScenarioName;
  if (!path) {
    alert('Open a collection first');
    return;
  }
  const label = currentScenario?.name || activeCollectionDocument?.name || fileLabel(path);
  if (!window.confirm(`Delete “${label}”? This cannot be undone.`)) {
    return;
  }
  try {
    await deletePath(isWorkspacePath(path) ? workspaceFilePath(workspaceCollectionId(path), collectionDocumentFilename(path)) : path);
    resetOpenDocuments();
    await loadScenarios();
    if (runOutput) {
      runOutput.textContent = 'Collection deleted.';
    }
  } catch (error) {
    alert(error.message || String(error));
  }
}

async function deleteOpenScenario() {
  const path = selectedScenarioName;
  if (!path || isCollectionDocument(currentScenario)) {
    alert('Open a scenario first');
    return;
  }
  const label = fileLabel(path);
  if (!window.confirm(`Delete scenario “${label}”? This cannot be undone.`)) {
    return;
  }
  try {
    const result = await deletePath(path);
    selectedScenarioName = null;
    currentScenario = null;
    selectedStepIndex = -1;
    await loadScenarios();
    const collectionPath = result.collection_path || result.suite_path || activeCollectionPath;
    if (collectionPath) {
      await reopenScenarioFile(collectionPath);
    }
    if (runOutput) {
      runOutput.textContent = 'Scenario deleted.';
    }
  } catch (error) {
    alert(error.message || String(error));
  }
}

async function copyOpenCollection() {
  try {
    const source = activeCollectionPath || selectedScenarioName;
    if (!source) {
      alert('Open a collection first');
      return;
    }
    if (isWorkspacePath(source)) {
      alert('This collection is already in your workspace.');
      return;
    }
    if (!authState.authenticated) {
      throw new Error(requireSignInMessage());
    }
    await copyOpenCollectionToWorkspace();
  } catch (error) {
    alert(error.message || String(error));
  }
}

function closeExplorerMenu() {
  const menu = document.getElementById('explorer-menu');
  if (menu) {
    menu.classList.add('hidden');
    menu.innerHTML = '';
    delete menu.dataset.path;
  }
}

function closeNewMenu() {
  const menu = document.getElementById('new-item-menu');
  const button = document.getElementById('new-item');
  if (menu) {
    menu.classList.add('hidden');
  }
  if (button) {
    button.setAttribute('aria-expanded', 'false');
  }
}

function toggleNewMenu() {
  if (!authState.authenticated) {
    alert(requireSignInMessage());
    return;
  }
  const menu = document.getElementById('new-item-menu');
  const button = document.getElementById('new-item');
  if (!menu || !button) {
    return;
  }
  const open = menu.classList.contains('hidden');
  closeExplorerMenu();
  if (open) {
    updateNewMenuHint();
    menu.classList.remove('hidden');
    button.setAttribute('aria-expanded', 'true');
  } else {
    closeNewMenu();
  }
}

function showExplorerMenu(event, target, anchor) {
  const menu = document.getElementById('explorer-menu');
  if (!menu) {
    return;
  }
  const items = explorerMenuItems(target);
  if (items.length === 0) {
    return;
  }
  if (menu.dataset.path === target.path && !menu.classList.contains('hidden')) {
    closeExplorerMenu();
    return;
  }
  closeNewMenu();
  menu.innerHTML = '';
  menu.dataset.path = target.path;
  items.forEach((item) => {
    if (item.separator) {
      const sep = document.createElement('div');
      sep.className = 'menu-sep';
      sep.textContent = item.label || '';
      menu.appendChild(sep);
      return;
    }
    const button = document.createElement('button');
    button.type = 'button';
    button.setAttribute('role', 'menuitem');
    button.textContent = item.label;
    if (item.danger) {
      button.classList.add('is-danger');
    }
    button.addEventListener('click', () => {
      closeExplorerMenu();
      void item.action();
    });
    menu.appendChild(button);
  });
  menu.classList.remove('hidden');
  const rect = (anchor || event.currentTarget)?.getBoundingClientRect?.();
  const left = rect ? rect.right - menu.offsetWidth : event.clientX;
  const top = rect ? rect.bottom + 4 : event.clientY;
  menu.style.left = `${Math.min(Math.max(8, left), window.innerWidth - menu.offsetWidth - 8)}px`;
  menu.style.top = `${Math.min(Math.max(8, top), window.innerHeight - menu.offsetHeight - 8)}px`;
}

function explorerMenuItems(target) {
  const items = [];
  if (target.kind === 'folder') {
    if (target.source === 'workspace') {
      items.push({label: 'New collection', action: () => createExplorerItem('collection', target.path)});
      items.push({label: 'New folder', action: () => createExplorerItem('folder', target.path)});
      if (target.path.startsWith('ws-folder/')) {
        items.push({label: 'Rename', action: () => renameExplorerItem(target)});
        items.push({separator: true, label: 'Order'});
        items.push({label: 'Move up', action: () => reorderByDelta('folders', target.parent || 'workspace', target.path, -1)});
        items.push({label: 'Move down', action: () => reorderByDelta('folders', target.parent || 'workspace', target.path, 1)});
        items.push({separator: true});
        items.push({label: 'Delete folder', danger: true, action: () => deleteExplorerFolder(target)});
      }
    }
    return items;
  }
  if (target.kind === 'collection') {
    items.push({label: 'Export', action: () => exportCollectionForBruno(target.path)});
    const permission = target.permission || (target.source === 'shared' ? 'read' : 'owner');
    const canEdit = permission === 'owner' || permission === 'edit';
    const isOwner = permission === 'owner' && target.source !== 'shared';
    if (isOwner && target.source === 'workspace') {
      items.push({label: 'Share…', action: () => openSharesDialog(target.path)});
    }
    if (isWorkspacePath(target.path)) {
      if (target.source === 'shared' || !isOwner) {
        items.push({label: 'Copy to my workspace', action: () => forkCollectionPath(target.path)});
      }
      if (canEdit) {
        items.push({label: 'Import…', action: () => startCollectionImport(target.path)});
        items.push({label: 'New scenario', action: () => createExplorerItem('scenario', target.path)});
      }
      if (isOwner && target.source_collection_id) {
        items.push({label: 'Compare with source', action: () => diffCollectionPath(target.path)});
        items.push({label: 'Pull updates', action: () => syncCollectionPath(target.path)});
      }
      if (isOwner) {
        items.push({label: 'Rename', action: () => renameExplorerItem(target)});
        items.push({separator: true, label: 'Order'});
        items.push({label: 'Move up', action: () => reorderByDelta('collections', target.parent, target.path, -1)});
        items.push({label: 'Move down', action: () => reorderByDelta('collections', target.parent, target.path, 1)});
        items.push({separator: true});
        items.push({label: 'Delete', danger: true, action: () => deleteExplorerCollection(target.path)});
      }
    } else {
      items.push({label: 'Copy to workspace', action: () => copyCollectionPath(target.path)});
    }
    return items;
  }
  items.push({label: 'Copy to another collection…', action: () => copyScenarioToCollection(target.path, target.parent)});
  if (isWorkspacePath(target.path)) {
    const permission = target.permission || findTreeNode(target.parent)?.permission || 'owner';
    const canEdit = permission === 'owner' || permission === 'edit';
    if (canEdit) {
      items.push({label: 'Rename', action: () => renameExplorerItem(target)});
      items.push({separator: true, label: 'Order'});
      items.push({label: 'Move up', action: () => reorderByDelta('scenarios', target.parent, target.path, -1)});
      items.push({label: 'Move down', action: () => reorderByDelta('scenarios', target.parent, target.path, 1)});
      items.push({separator: true});
      items.push({label: 'Delete scenario', danger: true, action: () => deleteExplorerScenario(target.path)});
    }
  }
  return items;
}

function siblingPaths(kind, parent) {
  if (kind === 'folders') {
    return (findTreeNode(parent)?.children || []).filter((node) => node.type === 'dir').map((node) => node.path);
  }
  if (kind === 'collections') {
    return (findTreeNode(parent)?.children || []).filter((node) => isCollectionNode(node)).map((node) => node.path);
  }
  const collection = findTreeNode(parent);
  const folder = parentFolderPath(parent);
  return (collection?.members || []).map((name) => joinPath(folder, name));
}

function movePath(items, path, deltaOrBefore) {
  const next = items.slice();
  const from = next.indexOf(path);
  if (from < 0) {
    return null;
  }
  if (typeof deltaOrBefore === 'number') {
    const to = from + deltaOrBefore;
    if (to < 0 || to >= next.length) {
      return null;
    }
    const [taken] = next.splice(from, 1);
    next.splice(to, 0, taken);
    return next;
  }
  const before = next.indexOf(deltaOrBefore);
  if (before < 0) {
    return null;
  }
  const [taken] = next.splice(from, 1);
  const insertAt = next.indexOf(deltaOrBefore);
  next.splice(insertAt, 0, taken);
  return next;
}

async function persistOrder(kind, parent, items) {
  await api('/api/explorer/reorder', {
    method: 'POST',
    body: JSON.stringify({kind, parent, items}),
  });
  await loadScenarios();
}

async function reorderByDelta(kind, parent, path, delta) {
  const next = movePath(siblingPaths(kind, parent), path, delta);
  if (!next) {
    return;
  }
  try {
    await persistOrder(kind, parent, next);
  } catch (error) {
    alert(error.message || String(error));
  }
}

async function reorderDropped(source, target) {
  const kind = source.kind === 'folder' ? 'folders' : source.kind === 'collection' ? 'collections' : 'scenarios';
  const next = movePath(siblingPaths(kind, source.parent), source.path, target.path);
  if (!next) {
    return;
  }
  try {
    await persistOrder(kind, source.parent, next);
  } catch (error) {
    alert(error.message || String(error));
  }
}

async function moveCollectionToFolder(path, destination) {
  try {
    await api('/api/explorer/move-collection', {
      method: 'POST',
      body: JSON.stringify({path, destination}),
    });
    expandFolderPath(destination);
    await loadScenarios();
  } catch (error) {
    alert(error.message || String(error));
  }
}

async function moveFolderToFolder(path, destination) {
  try {
    const moved = await api('/api/explorer/move-folder', {
      method: 'POST',
      body: JSON.stringify({path, destination}),
    });
    expandFolderPath(moved.path || destination);
    selectedFolderPath = moved.path || destination;
    await loadScenarios();
  } catch (error) {
    alert(error.message || String(error));
  }
}

function closeAskDialog(value) {
  const dialog = document.getElementById('ask-dialog');
  const confirm = document.getElementById('ask-dialog-confirm');
  if (confirm) {
    confirm.disabled = false;
  }
  if (dialog) {
    dialog.classList.add('hidden');
  }
  const resolve = askDialogResolver;
  askDialogResolver = null;
  if (resolve) {
    resolve(value);
  }
}

function askUser({title, help = '', value = '', confirmLabel = 'OK', options = null, emptyLabel = 'Nothing to choose.'}) {
  const dialog = document.getElementById('ask-dialog');
  const titleEl = document.getElementById('ask-dialog-title');
  const helpEl = document.getElementById('ask-dialog-help');
  const input = document.getElementById('ask-dialog-input');
  const inputWrap = document.getElementById('ask-dialog-input-wrap');
  const list = document.getElementById('ask-dialog-list');
  const confirm = document.getElementById('ask-dialog-confirm');
  if (!dialog || !input) {
    return Promise.resolve(window.prompt(title, value));
  }
  return new Promise((resolve) => {
    askDialogResolver = resolve;
    titleEl.textContent = title;
    helpEl.textContent = help;
    helpEl.classList.toggle('hidden', !help);
    confirm.textContent = confirmLabel;
    askDialogValue = options?.[0]?.value || value || '';
    input.value = value;
    if (options) {
      inputWrap.classList.add('hidden');
      list.classList.remove('hidden');
      list.innerHTML = '';
      if (options.length === 0) {
        const empty = document.createElement('p');
        empty.className = 'muted';
        empty.textContent = emptyLabel;
        list.appendChild(empty);
        confirm.disabled = true;
      } else {
        confirm.disabled = false;
        options.forEach((option) => {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = `picker-item${option.value === askDialogValue ? ' is-active' : ''}`;
          button.textContent = option.label;
          button.addEventListener('click', () => {
            askDialogValue = option.value;
            list.querySelectorAll('.picker-item').forEach((el) => el.classList.toggle('is-active', el === button));
          });
          list.appendChild(button);
        });
      }
    } else {
      confirm.disabled = false;
      inputWrap.classList.remove('hidden');
      list.classList.add('hidden');
      list.innerHTML = '';
    }
    dialog.classList.remove('hidden');
    window.setTimeout(() => {
      if (!options) {
        input.focus();
        input.select();
      }
    }, 0);
  });
}

async function createExplorerItem(kind, explicitTarget) {
  if (!authState.authenticated) {
    alert(requireSignInMessage());
    return;
  }
  const ctx = explorerCreateContext();
  if (kind === 'scenario' && !explicitTarget && !ctx.collectionPath) {
    const collectionName = await askUser({
      title: 'New collection',
      help: 'No workspace collection is selected, so a collection will be created first.',
      value: 'Untitled',
      confirmLabel: 'Continue',
    });
    if (!collectionName) {
      return;
    }
    const scenarioName = await askUser({
      title: 'New scenario',
      value: 'new_scenario',
      confirmLabel: 'Create',
    });
    if (!scenarioName) {
      return;
    }
    try {
      const collection = await api('/api/explorer/create', {
        method: 'POST',
        body: JSON.stringify({kind: 'collection', name: collectionName, target: ctx.folderPath}),
      });
      const created = await api('/api/explorer/create', {
        method: 'POST',
        body: JSON.stringify({kind: 'scenario', name: scenarioName, target: collection.path}),
      });
      expandedFolders.add(ctx.folderPath);
      expandedCollections.add(collection.path);
      await loadScenarios();
      await reopenScenarioFile(created.path, {fromCollection: true});
    } catch (error) {
      alert(error.message || String(error));
    }
    return;
  }
  const folderTarget = (explicitTarget && isWorkspaceFolderPath(explicitTarget))
    ? explicitTarget
    : ctx.folderPath;
  const folderLabel = findTreeNode(folderTarget)?.name || ctx.folderLabel || 'My workspace';
  const defaults = {
    folder: {title: 'New folder', value: 'folder', help: `Adds a folder in ${folderLabel}.`},
    collection: {title: 'New collection', value: 'Untitled', help: `Adds a collection in ${folderLabel}.`},
    scenario: {title: 'New scenario', value: 'new_scenario', help: `Adds a scenario to ${fileLabel(explicitTarget || ctx.collectionLabel)}.`},
  };
  const name = await askUser({...defaults[kind], confirmLabel: 'Create'});
  if (!name) {
    return;
  }
  const target = kind === 'scenario'
    ? (explicitTarget || ctx.collectionPath)
    : folderTarget;
  try {
    const created = await api('/api/explorer/create', {
      method: 'POST',
      body: JSON.stringify({kind, name, target}),
    });
    if (kind === 'folder') {
      selectedFolderPath = created.path;
      expandFolderPath(created.path);
      expandFolderPath(target);
      await loadScenarios();
      return;
    }
    if (kind === 'collection') {
      expandedFolders.add(target);
      expandedCollections.add(created.path);
      await loadScenarios();
      await reopenScenarioFile(created.path);
      return;
    }
    expandedCollections.add(created.collection_path || created.suite_path || target);
    await loadScenarios();
    if (created.collection_path || created.suite_path) {
      await reopenScenarioFile(created.collection_path || created.suite_path);
    }
    await reopenScenarioFile(created.path, {fromCollection: true});
  } catch (error) {
    alert(error.message || String(error));
  }
}

async function copyScenarioToCollection(source, currentCollectionPath) {
  if (!authState.authenticated) {
    alert(requireSignInMessage());
    return;
  }
  const options = collectCollectionNodes()
    .filter((node) => isWorkspacePath(node.path) && node.path !== currentCollectionPath)
    .map((node) => ({
      value: node.path,
      label: node.folder ? `${node.folder} / ${node.name}` : node.name,
    }));
  const destination = await askUser({
    title: 'Copy scenario',
    help: options.length ? 'Choose a workspace collection.' : '',
    confirmLabel: 'Copy',
    options,
    emptyLabel: 'Copy a collection into your workspace first.',
  });
  if (!destination) {
    return;
  }
  try {
    const copied = await api('/api/explorer/copy-scenario', {
      method: 'POST',
      body: JSON.stringify({source, destination}),
    });
    expandedCollections.add(copied.collection_path || copied.suite_path || destination);
    await loadScenarios();
    await reopenScenarioFile(copied.collection_path || copied.suite_path || destination);
    await reopenScenarioFile(copied.path, {fromCollection: true});
    if (runOutput) {
      runOutput.textContent = 'Scenario copied.';
    }
  } catch (error) {
    alert(error.message || String(error));
  }
}

async function copyCollectionPath(path) {
  try {
    if (!authState.authenticated) {
      throw new Error(requireSignInMessage());
    }
    if (isWorkspacePath(path)) {
      throw new Error('This collection is already in your workspace.');
    }
    const cloned = await clonePathToWorkspace(path);
    expandedFolders.add('workspace');
    expandedCollections.add(cloned.path);
    await loadScenarios();
    await reopenScenarioFile(cloned.path);
    if (runOutput) {
      runOutput.textContent = 'Copied to your workspace.';
    }
  } catch (error) {
    alert(error.message || String(error));
  }
}

function userPickerLabel(user) {
  const name = String(user?.name || '').trim();
  const email = String(user?.email || '').trim();
  if (name && email) {
    return `${name} (${email})`;
  }
  return name || email || String(user?.id || 'User');
}

function parseShareEnvironmentChoice(value) {
  return String(value || '').toLowerCase() === 'yes';
}

function closeSharesDialog() {
  document.getElementById('shares-dialog')?.classList.add('hidden');
}

async function updateShareSettings(path, userId, patch) {
  return api('/api/explorer/collection-shares', {
    method: 'PATCH',
    body: JSON.stringify({path, user_id: userId, ...patch}),
  });
}

async function revokeShare(path, userId) {
  const query = new URLSearchParams({path, user_id: userId});
  return api(`/api/explorer/collection-shares?${query.toString()}`, {method: 'DELETE'});
}

function fillSelect(select, options, selectedValue) {
  select.innerHTML = '';
  options.forEach((option) => {
    const el = document.createElement('option');
    el.value = option.value;
    el.textContent = option.label;
    if (String(option.value) === String(selectedValue)) {
      el.selected = true;
    }
    select.appendChild(el);
  });
}

function createShareField(labelText, control) {
  const label = document.createElement('label');
  label.className = 'share-row-field';
  const caption = document.createElement('span');
  caption.textContent = labelText;
  label.append(caption, control);
  return label;
}

function renderSharesDialog(path, shares, users) {
  const list = document.getElementById('shares-dialog-list');
  if (!list) {
    return;
  }
  list.innerHTML = '';
  const sharedIds = new Set((shares || []).map((share) => share.user_id));

  (shares || []).forEach((share) => {
    const row = document.createElement('div');
    row.className = 'share-row';

    const identity = document.createElement('div');
    identity.className = 'share-row-identity';
    const name = document.createElement('strong');
    name.textContent = share.user_name || share.user_id;
    identity.appendChild(name);
    if (share.user_email && share.user_email !== share.user_name) {
      const email = document.createElement('span');
      email.className = 'muted';
      email.textContent = share.user_email;
      identity.appendChild(email);
    }

    const permissionSelect = document.createElement('select');
    fillSelect(permissionSelect, [
      {value: 'read', label: 'Can view'},
      {value: 'edit', label: 'Can edit'},
    ], share.permission || 'read');
    permissionSelect.addEventListener('change', () => {
      void updateShareSettings(path, share.user_id, {permission: permissionSelect.value})
        .then((result) => {
          share.permission = result.share?.permission || permissionSelect.value;
        })
        .catch((error) => {
          alert(error.message || String(error));
          permissionSelect.value = share.permission || 'read';
        });
    });

    const envSelect = document.createElement('select');
    fillSelect(envSelect, [
      {value: 'no', label: 'No'},
      {value: 'yes', label: 'Yes'},
    ], share.share_environment ? 'yes' : 'no');
    envSelect.addEventListener('change', () => {
      const shareEnvironment = parseShareEnvironmentChoice(envSelect.value);
      void updateShareSettings(path, share.user_id, {share_environment: shareEnvironment})
        .then((result) => {
          share.share_environment = Boolean(result.share?.share_environment);
        })
        .catch((error) => {
          alert(error.message || String(error));
          envSelect.value = share.share_environment ? 'yes' : 'no';
        });
    });

    const withdraw = document.createElement('button');
    withdraw.type = 'button';
    withdraw.className = 'ghost danger-subtle';
    withdraw.textContent = 'Withdraw';
    withdraw.addEventListener('click', () => {
      if (!window.confirm(`Withdraw share from ${share.user_name || 'this user'}?`)) {
        return;
      }
      void revokeShare(path, share.user_id)
        .then(() => openSharesDialog(path))
        .catch((error) => alert(error.message || String(error)));
    });

    row.append(
      identity,
      createShareField('Access', permissionSelect),
      createShareField('Shared environment', envSelect),
      withdraw,
    );
    list.appendChild(row);
  });

  const availableUsers = (users || []).filter((user) => !sharedIds.has(user.id));
  const addRow = document.createElement('div');
  addRow.className = 'share-row share-row-add';

  const personSelect = document.createElement('select');
  if (!availableUsers.length) {
    fillSelect(personSelect, [{value: '', label: 'No more users to share with'}], '');
    personSelect.disabled = true;
  } else {
    fillSelect(
      personSelect,
      availableUsers.map((user) => ({value: user.id, label: userPickerLabel(user)})),
      availableUsers[0].id,
    );
  }

  const permissionSelect = document.createElement('select');
  fillSelect(permissionSelect, [
    {value: 'read', label: 'Can view'},
    {value: 'edit', label: 'Can edit'},
  ], 'read');

  const envSelect = document.createElement('select');
  fillSelect(envSelect, [
    {value: 'no', label: 'No'},
    {value: 'yes', label: 'Yes'},
  ], 'no');

  const addButton = document.createElement('button');
  addButton.type = 'button';
  addButton.className = 'primary';
  addButton.textContent = 'Add';
  addButton.disabled = !availableUsers.length;
  addButton.addEventListener('click', () => {
    const userId = personSelect.value;
    if (!userId) {
      return;
    }
    addButton.disabled = true;
    void api('/api/explorer/share-collection', {
      method: 'POST',
      body: JSON.stringify({
        path,
        user_id: userId,
        permission: permissionSelect.value || 'read',
        share_environment: parseShareEnvironmentChoice(envSelect.value),
      }),
    })
      .then((shared) => {
        expandedFolders.add('shared');
        if (runOutput) {
          const envLabel = shared.share_environment ? 'environment shared' : 'environment not shared';
          runOutput.textContent =
            `Shared “${shared.name || fileLabel(path)}” with ${shared.recipient_name || 'user'} ` +
            `(${shared.permission || 'read'}, ${envLabel}).`;
        }
        return Promise.all([loadScenarios(), openSharesDialog(path)]);
      })
      .catch((error) => {
        addButton.disabled = false;
        alert(error.message || String(error));
      });
  });

  addRow.append(
    createShareField('Person', personSelect),
    createShareField('Access', permissionSelect),
    createShareField('Shared environment', envSelect),
    addButton,
  );
  list.appendChild(addRow);
}

async function openSharesDialog(path) {
  try {
    if (!authState.authenticated) {
      throw new Error(requireSignInMessage());
    }
    const sharePath = path || activeCollectionPath || selectedScenarioName || '';
    if (!isWorkspacePath(sharePath)) {
      throw new Error('Copy the collection to your workspace first, then share it from My workspace.');
    }
    const dialog = document.getElementById('shares-dialog');
    const title = document.getElementById('shares-dialog-title');
    const help = document.getElementById('shares-dialog-help');
    if (!dialog) {
      throw new Error('Shares dialog is unavailable.');
    }
    const [shareData, usersData] = await Promise.all([
      api(`/api/explorer/collection-shares?path=${encodeURIComponent(sharePath)}`),
      api('/api/users'),
    ]);
    if (title) {
      title.textContent = `Share: ${shareData.name || fileLabel(sharePath)} ...`;
    }
    if (help) {
      help.textContent = 'Existing shares below. Use the last row to add someone.';
    }
    renderSharesDialog(sharePath, shareData.shares || [], usersData.users || []);
    dialog.dataset.path = sharePath;
    dialog.classList.remove('hidden');
  } catch (error) {
    alert(error.message || String(error));
  }
}

async function forkCollectionPath(path) {
  try {
    if (!authState.authenticated) {
      throw new Error(requireSignInMessage());
    }
    const forked = await api('/api/explorer/fork-collection', {
      method: 'POST',
      body: JSON.stringify({path}),
    });
    expandedFolders.add('workspace');
    expandedCollections.add(forked.path);
    await loadScenarios();
    await reopenScenarioFile(forked.path);
    if (runOutput) {
      runOutput.textContent = `Copied “${forked.name || fileLabel(path)}” to your workspace (linked to source for updates).`;
    }
  } catch (error) {
    alert(error.message || String(error));
  }
}

function formatDiffSummary(diff) {
  if (!diff || diff.identical) {
    return 'No differences from the source collection.';
  }
  const parts = [];
  if (diff.added_in_source?.length) {
    parts.push(`+${diff.added_in_source.length} in source`);
  }
  if (diff.removed_in_source?.length) {
    parts.push(`-${diff.removed_in_source.length} only local`);
  }
  if (diff.changed_scenarios?.length) {
    parts.push(`${diff.changed_scenarios.length} scenario(s) changed`);
  }
  if (diff.meta_changed?.length) {
    parts.push(`meta: ${diff.meta_changed.join(', ')}`);
  }
  return parts.join(' · ') || 'Differences found.';
}

async function diffCollectionPath(path) {
  try {
    if (!authState.authenticated) {
      throw new Error(requireSignInMessage());
    }
    const diff = await api(`/api/explorer/diff-collection?path=${encodeURIComponent(path)}`);
    if (runOutput) {
      runOutput.textContent = formatDiffSummary(diff);
    }
  } catch (error) {
    alert(error.message || String(error));
  }
}

async function syncCollectionPath(path) {
  try {
    if (!authState.authenticated) {
      throw new Error(requireSignInMessage());
    }
    if (!window.confirm('Pull updates from the linked source? Local scenario changes will be overwritten.')) {
      return;
    }
    const result = await api('/api/explorer/sync-collection', {
      method: 'POST',
      body: JSON.stringify({path}),
    });
    await loadScenarios();
    await reopenScenarioFile(result.path || path);
    if (runOutput) {
      runOutput.textContent = `Pulled updates. ${formatDiffSummary(result.diff)}`;
    }
  } catch (error) {
    alert(error.message || String(error));
  }
}

function filenameFromContentDisposition(header) {
  const value = String(header || '');
  const utf = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(value);
  if (utf) {
    try {
      return decodeURIComponent(utf[1].trim());
    } catch (_error) {
      return utf[1].trim();
    }
  }
  const plain = /filename\s*=\s*"([^"]+)"/i.exec(value) || /filename\s*=\s*([^;]+)/i.exec(value);
  return plain ? plain[1].trim() : '';
}

async function exportCollectionForBruno(path) {
  try {
    if (!authState.authenticated) {
      throw new Error(requireSignInMessage());
    }
    const url = `/api/explorer/export-bruno?path=${encodeURIComponent(path)}&_=${Date.now()}`;
    const response = await fetch(url, {cache: 'no-store', credentials: 'same-origin'});
    if (response.status === 401 && authState.oidc_enabled) {
      window.location.href = '/login';
      throw new Error('Sign in required');
    }
    if (!response.ok) {
      const body = await response.text();
      throw new Error(body || `Export failed: ${response.status}`);
    }
    const blob = await response.blob();
    const filename = filenameFromContentDisposition(response.headers.get('Content-Disposition')) || 'collection.bruno.json';
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
    if (runOutput) {
      runOutput.textContent = `Exported ${filename} for Bruno import.`;
    }
  } catch (error) {
    alert(error.message || String(error));
  }
}

function rewriteExplorerPath(value, oldPath, newPath) {
  const current = String(value || '');
  if (!oldPath || !newPath || current === '') {
    return current;
  }
  if (current === oldPath) {
    return newPath;
  }
  if (current.startsWith(`${oldPath}/`)) {
    return `${newPath}${current.slice(oldPath.length)}`;
  }
  return current;
}

function rewritePathSet(store, oldPath, newPath) {
  if (!store || !oldPath || !newPath || oldPath === newPath) {
    return;
  }
  const next = [...store].map((path) => rewriteExplorerPath(path, oldPath, newPath));
  store.clear();
  next.forEach((path) => store.add(path));
}

async function renameExplorerItem(target) {
  if (!authState.authenticated) {
    alert(requireSignInMessage());
    return;
  }
  const current = target.kind === 'scenario' ? fileLabel(target.name) : target.name;
  const name = await askUser({
    title: `Rename ${target.kind}`,
    value: current,
    confirmLabel: 'Rename',
  });
  if (!name || name === current) {
    return;
  }
  try {
    const renamed = await api('/api/explorer/rename', {
      method: 'POST',
      body: JSON.stringify({path: target.path, name}),
    });
    if (target.kind === 'folder') {
      rewritePathSet(expandedFolders, target.path, renamed.path);
      selectedFolderPath = rewriteExplorerPath(selectedFolderPath, target.path, renamed.path);
    }
    if (target.kind === 'scenario') {
      if (selectedScenarioName === target.path) {
        selectedScenarioName = renamed.path;
        if (scenarioNameInput) {
          scenarioNameInput.value = renamed.path;
        }
        if (currentScenario) {
          currentScenario.name = fileLabel(renamed.name || name);
        }
      }
    }
    if (target.kind === 'collection') {
      if (activeCollectionDocument) {
        activeCollectionDocument.name = renamed.name;
      }
      if (currentScenario && isCollectionDocument(currentScenario) && (activeCollectionPath === target.path || selectedScenarioName === target.path)) {
        currentScenario.name = renamed.name;
      }
    }
    await loadScenarios();
    if (renamed.kind === 'collection') {
      if (activeCollectionPath === renamed.path || selectedScenarioName === renamed.path) {
        await reopenScenarioFile(renamed.path);
      }
    } else if (renamed.kind === 'scenario' && selectedScenarioName === renamed.path) {
      await reopenScenarioFile(renamed.path, {fromCollection: true});
    }
    if (runOutput) {
      runOutput.textContent = `Renamed to ${renamed.name}.`;
    }
  } catch (error) {
    alert(error.message || String(error));
  }
}

async function deleteExplorerFolder(target) {
  if (!window.confirm(`Delete folder “${target.name}”? It must be empty.`)) {
    return;
  }
  try {
    await api(`/api/explorer/folder?path=${encodeURIComponent(target.path)}`, {method: 'DELETE'});
    if (selectedFolderPath === target.path) {
      selectedFolderPath = 'workspace';
    }
    await loadScenarios();
  } catch (error) {
    alert(error.message || String(error));
  }
}

async function deleteExplorerCollection(path) {
  const label = findTreeNode(path)?.name || fileLabel(path);
  if (!window.confirm(`Delete “${label}”? This cannot be undone.`)) {
    return;
  }
  try {
    await deletePath(isWorkspacePath(path) ? workspaceFilePath(workspaceCollectionId(path), collectionDocumentFilename(path)) : path);
    if (activeCollectionPath === path || selectedScenarioName === path) {
      resetOpenDocuments();
    }
    await loadScenarios();
  } catch (error) {
    alert(error.message || String(error));
  }
}

async function deleteExplorerScenario(path) {
  const label = fileLabel(path);
  if (!window.confirm(`Delete scenario “${label}”? This cannot be undone.`)) {
    return;
  }
  try {
    const result = await deletePath(path);
    if (selectedScenarioName === path) {
      selectedScenarioName = null;
      currentScenario = null;
      selectedStepIndex = -1;
    }
    await loadScenarios();
    if (result.collection_path || result.suite_path) {
      await reopenScenarioFile(result.collection_path || result.suite_path);
    }
  } catch (error) {
    alert(error.message || String(error));
  }
}

function startNewScenario() {
  void createExplorerItem('scenario');
}

let pendingImportCollectionPath = '';

function startCollectionImport(collectionPath) {
  const path = String(collectionPath || activeCollectionPath || '').trim();
  if (!path) {
    alert('Open a workspace collection before importing.');
    return;
  }
  if (!isWorkspacePath(path)) {
    alert('Import is only available for workspace collections. Copy the collection to your workspace first.');
    return;
  }
  if (!authState.authenticated) {
    alert(requireSignInMessage());
    return;
  }
  if (!importCollectionFileInput) {
    return;
  }
  pendingImportCollectionPath = path;
  importCollectionFileInput.value = '';
  importCollectionFileInput.click();
}

async function importCollectionFromFile(file, collectionPath) {
  if (!file) {
    return;
  }
  const path = String(collectionPath || pendingImportCollectionPath || activeCollectionPath || '').trim();
  const collectionId = workspaceCollectionId(path);
  if (!collectionId) {
    alert('Import is only available on a workspace collection.');
    return;
  }

  const form = new FormData();
  form.append('file', file);

  if (importCollectionButton) {
    importCollectionButton.disabled = true;
  }
  if (runOutput) {
    runOutput.textContent = 'Importing into collection...';
  }

  try {
    const importUrl = `/api/scenarios/import/file?collection_id=${encodeURIComponent(collectionId)}`;
    const response = await fetch(importUrl, {
      method: 'POST',
      credentials: 'same-origin',
      body: form,
    });

    if (!response.ok) {
      const message = await response.text();
      throw new Error(message || `Import failed: ${response.status}`);
    }

    const result = await response.json();
    await loadScenarios();
    const openPath = result.path || result.name;
    if (openPath) {
      await reopenScenarioFile(openPath);
    } else if (path) {
      await reopenScenarioFile(path);
    }
    if (runOutput) {
      runOutput.textContent = `Imported ${fileLabel(openPath || result.name || 'scenario')} with ${result.step_count} steps.`;
    }
  } catch (error) {
    if (runOutput) {
      runOutput.textContent = String(error);
    } else {
      alert(error.message || String(error));
    }
  } finally {
    if (importCollectionButton) {
      importCollectionButton.disabled = false;
    }
    if (importCollectionFileInput) {
      importCollectionFileInput.value = '';
    }
    pendingImportCollectionPath = '';
  }
}

function scenarioTotalsPassed(totals) {
  const t = totals || {};
  return (t.failure || 0) === 0 && (t.expected_mismatch || 0) === 0 && (t.success || 0) > 0;
}

function firstStepError(steps) {
  const failed = (steps || []).find((step) => step.failure || step.expected_mismatch);
  if (!failed) {
    return '';
  }
  return failed.last_error || (failed.last_status != null ? `status ${failed.last_status}` : '');
}

function passFailRows(items) {
  return items.map((item) => {
    const ok = item.ok;
    const error = ok ? '' : (item.error || '');
    return `<tr class="${ok ? 'row-pass' : 'row-fail'}">
      <td>${escapeHtml(item.name || '')}</td>
      <td>${ok ? 'pass' : 'fail'}</td>
      <td>${escapeHtml(error)}</td>
    </tr>`;
  }).join('');
}

function renderPassFailList(summary) {
  const scenario = summary?.scenario;
  if (!scenario) {
    return '<p class="muted">No pass / fail results from this run.</p>';
  }
  const children = Array.isArray(scenario.scenarios) ? scenario.scenarios : null;
  if (children) {
    const rows = passFailRows(children.map((child) => ({
      name: child.name || child.file || '',
      ok: scenarioTotalsPassed(child.totals),
      error: firstStepError(child.steps),
    })));
    return `<table class="results-table">
      <thead><tr><th>Scenario</th><th>Result</th><th>Error</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  }
  const steps = Array.isArray(scenario.steps) ? scenario.steps : [];
  if (!steps.length) {
    return '<p class="muted">No steps in this run.</p>';
  }
  const rows = passFailRows(steps.map((step) => ({
    name: step.name || '',
    ok: (step.failure || 0) === 0 && (step.expected_mismatch || 0) === 0,
    error: String(step.last_error || ''),
  })));
  return `<table class="results-table">
    <thead><tr><th>Step</th><th>Result</th><th>Error</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

function renderLastRunResults(summary) {
  if (!reportText) {
    return;
  }
  const scenario = summary?.scenario || {};
  const totals = scenario.totals || {};
  const children = Array.isArray(scenario.scenarios) ? scenario.scenarios : null;
  if (reportSummary) {
    if (!summary || !scenario || (!children && !Array.isArray(scenario.steps))) {
      reportSummary.innerHTML = '';
    } else if (children) {
      const failed = totals.failed || 0;
      const passed = totals.passed ?? Math.max(children.length - failed, 0);
      reportSummary.innerHTML = [
        `<div class="metric-box"><span>Passed</span><strong>${passed}</strong></div>`,
        `<div class="metric-box${failed ? ' metric-fail' : ''}"><span>Failed</span><strong>${failed}</strong></div>`,
      ].join('');
    } else {
      const failed = (totals.failure || 0) + (totals.expected_mismatch || 0);
      const passed = (scenario.steps || []).filter((step) => (step.failure || 0) === 0 && (step.expected_mismatch || 0) === 0).length;
      reportSummary.innerHTML = [
        `<div class="metric-box"><span>Passed</span><strong>${passed}</strong></div>`,
        `<div class="metric-box${failed ? ' metric-fail' : ''}"><span>Failed</span><strong>${failed}</strong></div>`,
      ].join('');
    }
  }
  reportText.innerHTML = renderPassFailList(summary);
}

function renderSequenceTestResult(result) {
  if (!sequenceTestOutput) {
    return;
  }
  if (typeof result === 'string') {
    sequenceTestOutput.textContent = result;
    return;
  }
  const lines = [
    `${result.passed || 0} passed / ${result.failed || 0} failed  ${result.base_url || ''}`.trim(),
  ];
  for (const step of result.steps || []) {
    const mark = step.success ? 'PASS' : 'FAIL';
    const extra = step.success
      ? `${step.response_status ?? ''} ${step.latency_ms ?? ''}ms`.trim()
      : (step.error || `status ${step.response_status ?? '?'}`);
    lines.push(`${mark}  ${step.name || `step_${step.index + 1}`}  ${extra}`);
  }
  sequenceTestOutput.textContent = lines.join('\n');
}

function rememberStepContextVars(result) {
  const vars = result?.vars || result?.context?.vars;
  if (vars && typeof vars === 'object' && !Array.isArray(vars)) {
    lastStepContextVars = {...lastStepContextVars, ...vars};
  }
}

function renderStepCurl(text, canCopy = false) {
  if (stepCurlOutput) {
    stepCurlOutput.textContent = text;
  }
  if (copyStepCurlButton) {
    copyStepCurlButton.disabled = !canCopy;
    if (canCopy) {
      lastCopiedCurl = text;
      copyStepCurlButton.textContent = 'Copy';
    }
  }
}

function withActiveCollection(payload) {
  if (activeCollectionDocument) {
    payload.collection = activeCollectionDocument;
    // Legacy key for run/step APIs that still read `suite`.
    payload.suite = activeCollectionDocument;
  }
  if (selectedScenarioName && !isWorkspacePath(selectedScenarioName)) {
    payload.scenario_file = `./examples/${selectedScenarioName}`;
  }
  return payload;
}

function stepCurlPayload(hydratePrior) {
  const target = getRunTarget();
  return withActiveCollection({
    scenario: currentScenario,
    step_index: selectedStepIndex,
    base_url: requestBaseUrl(),
    selected_environment: getSelectedEnvironmentName(),
    environment_overrides: environmentOverridesPayload(),
    context_vars: lastStepContextVars,
    hydrate_prior: hydratePrior,
  });
}

function stepCurlCacheKey() {
  const target = getRunTarget();
  const name = selectedScenarioName || currentScenario?.name || '';
  return `${name}|${selectedStepIndex}|${target.host}|${target.port}|${getSelectedEnvironmentName()}|${JSON.stringify(environmentOverridesPayload())}`;
}

function scheduleStepCurlPreview() {
  if (!stepCurlOutput) {
    return;
  }
  if (!currentScenario || selectedStepIndex < 0) {
    renderStepCurl('Select a step to see curl.');
    return;
  }
  window.clearTimeout(stepCurlTimer);
  stepCurlTimer = window.setTimeout(() => {
    void refreshStepCurl();
  }, 250);
}

async function refreshStepCurl() {
  if (!currentScenario || selectedStepIndex < 0) {
    renderStepCurl('Select a step to see curl.');
    return;
  }
  const generation = ++stepCurlGeneration;
  const cacheKey = stepCurlCacheKey();
  const hydratePrior = cacheKey !== lastHydratedCurlKey;
  renderStepCurl(hydratePrior ? 'Resolving placeholders…' : (stepCurlOutput.textContent || 'Expanding curl…'));
  try {
    const result = await api('/api/preview-step', {
      method: 'POST',
      body: JSON.stringify(stepCurlPayload(hydratePrior)),
    });
    if (generation !== stepCurlGeneration) {
      return;
    }
    rememberStepContextVars(result);
    lastHydratedCurlKey = cacheKey;
    const notes = (result.unresolved || []).length
      ? `\n\n# unresolved: ${(result.unresolved || []).join(', ')}`
      : '';
    renderStepCurl(`${result.curl || ''}${notes}`, Boolean(result.curl));
  } catch (error) {
    if (generation !== stepCurlGeneration) {
      return;
    }
    renderStepCurl(String(error));
  }
}

async function copyStepCurl() {
  const text = lastCopiedCurl || stepCurlOutput?.textContent || '';
  if (!text || !copyStepCurlButton) {
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    copyStepCurlButton.textContent = 'Copied';
    window.setTimeout(() => {
      if (copyStepCurlButton.textContent === 'Copied') {
        copyStepCurlButton.textContent = 'Copy';
      }
    }, 1200);
  } catch (error) {
    renderStepCurl(`${text}\n\n# copy failed: ${error}`);
  }
}

function sequenceTestPayload() {
  return withActiveCollection({
    scenario: currentScenario,
    base_url: requestBaseUrl(),
    selected_environment: getSelectedEnvironmentName(),
    environment_overrides: environmentOverridesPayload(),
  });
}

async function testAllSteps() {
  if (!currentScenario || !Array.isArray(currentScenario.steps) || currentScenario.steps.length === 0) {
    renderSequenceTestResult('Open a scenario with steps first.');
    return;
  }
  if (unsatisfiedEnvironmentNames().length) {
    renderSequenceTestResult('Set every required environment value before testing the sequence.');
    return;
  }
  if (testSequenceButton) {
    testSequenceButton.disabled = true;
  }
  renderSequenceTestResult('Running all steps...');
  try {
    const result = await api('/api/test-sequence', {
      method: 'POST',
      body: JSON.stringify(sequenceTestPayload()),
    });
    rememberStepContextVars(result);
    lastHydratedCurlKey = '';
    renderSequenceTestResult(result);
    scheduleStepCurlPreview();
  } catch (error) {
    renderSequenceTestResult(String(error));
  } finally {
    if (testSequenceButton) {
      testSequenceButton.disabled = !(currentScenario?.steps || []).length;
    }
  }
}

async function testSelectedStep() {
  if (!currentScenario || selectedStepIndex < 0) {
    renderTestStepResult('Select a step first.');
    return;
  }
  if (unsatisfiedEnvironmentNames().length) {
    renderTestStepResult('Set every required environment value before testing a step.');
    return;
  }
  testStepButton.disabled = true;
  renderTestStepResult('Testing selected step...');
  try {
    const result = await api('/api/test-step', {
      method: 'POST',
      body: JSON.stringify(withActiveCollection({
        scenario: currentScenario,
        step_index: selectedStepIndex,
        base_url: requestBaseUrl(),
        selected_environment: getSelectedEnvironmentName(),
        environment_overrides: environmentOverridesPayload(),
      })),
    });
    rememberStepContextVars(result);
    lastHydratedCurlKey = '';
    renderTestStepResult(result);
    scheduleStepCurlPreview();
  } catch (error) {
    renderTestStepResult(String(error));
  } finally {
    testStepButton.disabled = false;
  }
}

function setRunButtonsDisabled(disabled) {
  runInProgress = Boolean(disabled);
  updateRunAvailability();
}

async function startRun() {
  const runFile = activeCollectionPath || selectedScenarioName || scenarioNameInput.value.trim();
  if (!runFile) {
    alert('Open a collection first');
    return;
  }

  if (unsatisfiedEnvironmentNames().length) {
    alert('Set every required environment value before running tests.');
    return;
  }

  const {scheme, host, port: inferredPort} = getRunTarget();
  if (stepsNeedSharedBaseUrl() && !host) {
    alert('Select an environment with a server URL, or put a full URL on each step.');
    return;
  }
  if (host && isForbiddenApiHost(host)) {
    alert(ROUTABLE_HOST_HELP);
    return;
  }

  setRunButtonsDisabled(true);
  runSpinner.classList.remove('hidden');
  runOutput.textContent = 'Running tests…';

  let progressTimer = null;
  const startTime = Date.now();
  runProgressBar.style.transition = 'none';
  runProgressBar.style.width = '0%';
  runProgressTrack.setAttribute('aria-valuenow', 0);
  runProgressLabel.textContent = 'Running…';
  runProgressTime.textContent = '0 s';
  runProgressWrap.classList.remove('hidden');
  requestAnimationFrame(() => {
    runProgressBar.style.transition = 'width 0.8s linear';
  });
  progressTimer = setInterval(() => {
    const elapsed = (Date.now() - startTime) / 1000;
    const elapsedRounded = Math.round(elapsed);
    runProgressBar.style.width = `${Math.min(elapsed * 4, 90).toFixed(1)}%`;
    runProgressTime.textContent = `${elapsedRounded} s`;
  }, 500);

  const payload = {
    scenario_file: isWorkspacePath(runFile) ? runFile : `./examples/${runFile}`,
    scheme,
    host,
    port: inferredPort,
    scenario_users: 1,
    scenario_duration: 60,
    scenario_iterations: 1,
    scenario_environment: getSelectedEnvironmentName(),
    environment_overrides: environmentOverridesPayload(),
    regression: true,
  };
  let runFailed = false;
  try {
    const result = await api('/api/runs', {method: 'POST', body: JSON.stringify(payload)});
    const logText = stripAnsi(result.stdout || '').trim();
    runOutput.textContent = logText || 'Run completed.';
    renderLastRunResults(result.summary);
    if (result.status === 'completed_with_errors') {
      runFailed = true;
    }
  } catch (error) {
    runFailed = true;
    let msg = String(error);
    try {
      const inner = JSON.parse(msg.replace(/^Error:\s*/, ''));
      const detail = inner?.detail;
      if (detail?.summary) {
        renderLastRunResults(detail.summary);
      }
      if (detail?.stdout) msg = detail.stdout.replace(/\x1b\[[0-9;]*m/g, '').trim();
      else if (typeof detail === 'string') msg = detail;
    } catch { /* use raw msg */ }
    runOutput.textContent = `Run failed:\n${msg}`;
  } finally {
    if (progressTimer) clearInterval(progressTimer);
    runSpinner.classList.add('hidden');
    setRunButtonsDisabled(false);

    if (runFailed) {
      runProgressBar.style.transition = 'none';
      runProgressBar.classList.add('run-progress-bar--error');
      runProgressLabel.textContent = 'Failed';
    } else {
      runProgressBar.style.width = '100%';
      runProgressTrack.setAttribute('aria-valuenow', 100);
      runProgressLabel.textContent = 'Done';
    }
    setTimeout(() => {
      runProgressWrap.classList.add('hidden');
      runProgressBar.classList.remove('run-progress-bar--error');
    }, 3000);
  }
}

function getStoredTheme() {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY) || localStorage.getItem('lti.theme');
    return stored === 'light' || stored === 'dark' ? stored : THEME_DEFAULT;
  } catch {
    return THEME_DEFAULT;
  }
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const toggle = document.getElementById('theme-toggle');
  if (!toggle) {
    return;
  }
  const next = theme === 'dark' ? 'light' : 'dark';
  toggle.setAttribute('aria-label', `Switch to ${next} mode`);
  toggle.title = `Switch to ${next} mode`;
}

function initTheme() {
  applyTheme(getStoredTheme());
  const toggle = document.getElementById('theme-toggle');
  if (!toggle) {
    return;
  }
  toggle.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Ignore storage failures and still apply the theme for this session.
    }
    applyTheme(next);
  });
}

initTheme();

function displayUserName(user) {
  const name = String(user?.name || '').trim();
  const email = String(user?.email || '').trim();
  return name || email || 'Signed in';
}

function openAboutDialog() {
  const dialog = document.getElementById('about-dialog');
  const releaseEl = document.getElementById('about-release');
  if (!dialog) {
    return;
  }
  if (releaseEl) {
    const release = String(document.body?.dataset?.release || '').trim();
    releaseEl.textContent = release ? `Release ${release}` : '';
  }
  dialog.classList.remove('hidden');
}

function closeAboutDialog() {
  document.getElementById('about-dialog')?.classList.add('hidden');
}

function renderAuthBar() {
  const toggle = document.getElementById('auth-toggle');
  const userLabel = document.getElementById('auth-user');
  if (!toggle) {
    return;
  }
  if (!authState.oidc_enabled) {
    toggle.classList.add('hidden');
    userLabel?.classList.add('hidden');
    return;
  }
  toggle.classList.remove('hidden');
  if (authState.authenticated) {
    const label = displayUserName(authState.user);
    toggle.textContent = 'Sign out';
    toggle.title = `Signed in as ${label}`;
    if (userLabel) {
      userLabel.textContent = label;
      userLabel.title = label;
      userLabel.classList.remove('hidden');
    }
  } else {
    toggle.textContent = 'Sign in';
    toggle.title = 'Sign in';
    if (userLabel) {
      userLabel.textContent = '';
      userLabel.removeAttribute('title');
      userLabel.classList.add('hidden');
    }
  }
  const newItem = document.getElementById('new-item');
  if (newItem) {
    newItem.disabled = Boolean(authState.oidc_enabled && !authState.authenticated);
    newItem.title = newItem.disabled ? requireSignInMessage() : 'Create in your workspace';
  }
}

function toggleAuth() {
  if (!authState.oidc_enabled) {
    return;
  }
  window.location.href = authState.authenticated ? '/logout' : '/login';
}

async function boot() {
  try {
    authState = await api('/api/me');
  } catch {
    authState = {authenticated: false, oidc_enabled: false, user: null};
  }
  renderAuthBar();
  await loadScenarios();
  updateRunAvailability();
}

document.getElementById('refresh-scenarios').addEventListener('click', loadScenarios);
document.getElementById('new-item')?.addEventListener('click', (event) => {
  event.stopPropagation();
  toggleNewMenu();
});
document.getElementById('new-item-menu')?.addEventListener('click', (event) => {
  const kind = event.target?.dataset?.kind;
  if (!kind) {
    return;
  }
  closeNewMenu();
  void createExplorerItem(kind);
});
document.getElementById('ask-dialog-cancel')?.addEventListener('click', () => closeAskDialog(null));
document.getElementById('ask-dialog-cancel-top')?.addEventListener('click', () => closeAskDialog(null));
document.getElementById('ask-dialog')?.querySelector('.json-dialog-backdrop')?.addEventListener('click', () => closeAskDialog(null));
document.getElementById('ask-dialog-confirm')?.addEventListener('click', () => {
  const list = document.getElementById('ask-dialog-list');
  const input = document.getElementById('ask-dialog-input');
  if (list && !list.classList.contains('hidden')) {
    closeAskDialog(askDialogValue || null);
    return;
  }
  closeAskDialog((input?.value || '').trim() || null);
});
document.getElementById('ask-dialog-input')?.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    document.getElementById('ask-dialog-confirm')?.click();
  }
  if (event.key === 'Escape') {
    closeAskDialog(null);
  }
});
document.addEventListener('click', (event) => {
  const hit = event.target instanceof Element ? event.target : event.target?.parentElement;
  if (!hit?.closest('.new-menu-wrap')) {
    closeNewMenu();
  }
  if (!hit?.closest('.explorer-menu') && !hit?.closest('.tree-more')) {
    closeExplorerMenu();
  }
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    closeNewMenu();
    closeExplorerMenu();
    closeAboutDialog();
  }
});
document.getElementById('save-scenario').addEventListener('click', saveScenario);
document.getElementById('auth-toggle')?.addEventListener('click', toggleAuth);
document.getElementById('about-open')?.addEventListener('click', openAboutDialog);
document.getElementById('about-dialog-close')?.addEventListener('click', closeAboutDialog);
document.getElementById('about-dialog-close-top')?.addEventListener('click', closeAboutDialog);
document.getElementById('about-dialog')?.querySelector('.json-dialog-backdrop')?.addEventListener('click', closeAboutDialog);
if (copyCollectionButton) {
  copyCollectionButton.addEventListener('click', () => {
    void copyOpenCollection();
  });
}
if (shareCollectionButton) {
  shareCollectionButton.addEventListener('click', () => {
    void openSharesDialog(activeCollectionPath || selectedScenarioName);
  });
}
document.getElementById('shares-dialog-close')?.addEventListener('click', closeSharesDialog);
document.getElementById('shares-dialog-close-top')?.addEventListener('click', closeSharesDialog);
document.getElementById('shares-dialog')?.querySelector('.json-dialog-backdrop')?.addEventListener('click', closeSharesDialog);
if (deleteCollectionButton) {
  deleteCollectionButton.addEventListener('click', () => {
    void deleteOpenCollection();
  });
}
if (deleteScenarioButton) {
  deleteScenarioButton.addEventListener('click', () => {
    void deleteOpenScenario();
  });
}
if (saveCollectionButton) {
  saveCollectionButton.addEventListener('click', () => {
    void saveCollection();
  });
}
if (importCollectionButton) {
  importCollectionButton.addEventListener('click', () => {
    startCollectionImport(activeCollectionPath || selectedScenarioName);
  });
}
if (exportCollectionBrunoButton) {
  exportCollectionBrunoButton.addEventListener('click', () => {
    const collectionPath = activeCollectionPath || (isCollectionDocument(currentScenario) ? selectedScenarioName : '');
    if (!collectionPath) {
      alert('Open a collection before exporting.');
      return;
    }
    void exportCollectionForBruno(collectionPath);
  });
}
if (importCollectionFileInput) {
  importCollectionFileInput.addEventListener('change', async () => {
    const [file] = importCollectionFileInput.files || [];
    await importCollectionFromFile(file, pendingImportCollectionPath || activeCollectionPath);
  });
}
if (regressionRunButton) {
  regressionRunButton.addEventListener('click', () => startRun());
}
document.getElementById('add-step').addEventListener('click', addStep);
stepMoveUpButton.addEventListener('click', () => moveStep(selectedStepIndex, selectedStepIndex - 1));
stepMoveDownButton.addEventListener('click', () => moveStep(selectedStepIndex, selectedStepIndex + 1));
stepDeleteButton.addEventListener('click', () => {
  if (selectedStepIndex < 0) {
    return;
  }
  if (window.confirm('Are you sure?')) {
    deleteStep(selectedStepIndex);
  }
});
testStepButton.addEventListener('click', testSelectedStep);
if (testSequenceButton) {
  testSequenceButton.addEventListener('click', testAllSteps);
}
if (copyStepCurlButton) {
  copyStepCurlButton.addEventListener('click', copyStepCurl);
}
importStepCurlButton?.addEventListener('click', openCurlDialog);
document.getElementById('curl-dialog-cancel')?.addEventListener('click', closeCurlDialog);
document.getElementById('curl-dialog-cancel-top')?.addEventListener('click', closeCurlDialog);
document.getElementById('curl-dialog-apply')?.addEventListener('click', () => {
  void applyCurlDialog();
});
curlDialog?.addEventListener('click', (event) => {
  if (event.target === curlDialog || event.target.dataset.close === 'true') {
    closeCurlDialog();
  }
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && curlDialog && !curlDialog.classList.contains('hidden')) {
    closeCurlDialog();
  }
});
document.getElementById('clear-session-env')?.addEventListener('click', () => {
  clearSessionEnvOverrides();
  lastRequiredEnvNames = '';
  updateRunAvailability();
  lastHydratedCurlKey = '';
  scheduleStepCurlPreview();
});

bindScenarioField(scenarioBaseUrl, (scenario, element) => {
  scenario.base_url = element.value.trim();
  updateRunTargetHint();
});

scenarioEnvironmentSelect.addEventListener('change', () => {
  if (!currentScenario) {
    currentScenario = createEmptyScenario();
  }
  currentScenario.selected_environment = scenarioEnvironmentSelect.value;
  if (activeCollectionDocument) {
    activeCollectionDocument.selected_environment = scenarioEnvironmentSelect.value;
  }
  lastHydratedCurlKey = '';
  lastStepContextVars = {};
  lastRequiredEnvNames = '';
  loadedWorkspaceEnvKey = '';
  void loadWorkspaceEnvValues().finally(() => {
    renderScenarioBuilder();
  });
});


editRandomGeneratorsJsonButton.addEventListener('click', () => openJsonDialog('random_generators'));
editEnvironmentsJsonButton.addEventListener('click', () => openJsonDialog('environments'));
editStepHeadersJsonButton.addEventListener('click', () => openJsonDialog('step_headers'));
editStepJsonButton.addEventListener('click', () => openJsonDialog('step_json'));
editStepSaveJsonButton.addEventListener('click', () => openJsonDialog('step_save'));
editStepExpectedJsonButton.addEventListener('click', () => openJsonDialog('step_expected_json'));
jsonDialogCancelButton.addEventListener('click', closeJsonDialog);
jsonDialogCancelTopButton.addEventListener('click', closeJsonDialog);
jsonDialogSaveButton.addEventListener('click', saveJsonDialog);
jsonDialog.addEventListener('click', (event) => {
  if (event.target === jsonDialog || event.target.dataset.close === 'true') {
    closeJsonDialog();
  }
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !jsonDialog.classList.contains('hidden')) {
    closeJsonDialog();
  }
});

bindStepField(stepNameInput, (step, element) => {
  step.name = element.value;
});
bindStepField(stepMethodInput, (step, element) => {
  step.method = element.value;
});
bindStepField(stepPathInput, (step, element) => {
  step.path = element.value.trim();
  delete step.url;
});
bindStepField(stepTimeoutInput, (step, element) => {
  if (!element.value) {
    delete step.timeout;
    return;
  }
  step.timeout = Number(element.value);
});
bindStepField(stepExpectedStatusInput, (step, element) => {
  const parsed = parseExpectedStatus(element.value);
  if (parsed === undefined) {
    delete step.expected_status;
    return;
  }
  step.expected_status = parsed;
});
bindStepField(stepSaveResponseAsInput, (step, element) => {
  if (!element.value.trim()) {
    delete step.save_response_as;
    return;
  }
  step.save_response_as = element.value.trim();
});
stepFollowRedirectsInput?.addEventListener('change', () => {
  const step = getSelectedStep();
  if (!step) {
    return;
  }
  if (stepFollowRedirectsInput.checked) {
    step.follow_redirects = true;
  } else {
    delete step.follow_redirects;
  }
  renderScenarioBuilder();
});
stepStopOnFailureInput.addEventListener('change', () => {
  const step = getSelectedStep();
  if (!step) {
    return;
  }
  if (stepStopOnFailureInput.checked) {
    step.stop_on_failure = true;
  } else {
    delete step.stop_on_failure;
  }
  renderScenarioBuilder();
});

void boot();
