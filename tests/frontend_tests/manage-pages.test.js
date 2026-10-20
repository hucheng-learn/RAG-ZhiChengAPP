const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function setup(api) {
  const elements = new Map();
  const $ = key => {
    if (!elements.has(key)) elements.set(key, {});
    return elements.get(key);
  };
  const context = vm.createContext({$, api, notice() {}});
  const source = fs.readFileSync(path.join(__dirname, '../../page/app.js'), 'utf8');
  vm.runInContext(source.slice(source.indexOf('const managePageSize=10;'),
    source.indexOf('async function loadManage(')), context);
  vm.runInContext('async function loadManage(){return [documentPage,taskPage];}', context);
  return {$, context};
}

test('management lists have independent ten-item pages and disabled boundaries', async () => {
  const calls = [];
  const {$, context} = setup(async url => {
    calls.push(url);
    const offset = Number(new URL(url, 'http://test').searchParams.get('offset'));
    return {items: Array.from({length: offset === 0 ? 10 : 1}), has_more: offset === 0};
  });
  const first = await context.fetchManagePage('/documents', 0);
  context.renderManagePager('documents', first);
  assert.equal($('#documents-prev').disabled, true);
  assert.equal($('#documents-next').disabled, false);
  assert.equal($('#documents-page').textContent, '第 1 页 · 每页 10 条');
  assert.deepEqual(Array.from(await $('#documents-next').onclick()), [1, 0]);
  const last = await context.fetchManagePage('/documents', 1);
  context.renderManagePager('documents', last);
  assert.equal(last.items.length, 1);
  assert.equal($('#documents-next').disabled, true);
  assert.equal($('#documents-next').onclick(), undefined);
  assert.deepEqual(Array.from(await $('#tasks-next').onclick()), [1, 1]);
  assert.deepEqual(calls, ['/documents?limit=10&offset=0', '/documents?limit=10&offset=10']);
});

test('deleting the final row returns to a populated page and handles an empty list', async () => {
  const calls = [];
  const {$, context} = setup(async url => {
    calls.push(url);
    return {items: url.endsWith('offset=0') ? [{id: 'keep'}] : [], has_more: false};
  });
  const result = await context.fetchManagePage('/import-tasks', 2);
  assert.equal(result.page, 0);
  assert.equal(result.items[0].id, 'keep');
  assert.equal(calls.length, 3);
  context.renderManagePager('tasks', {page: 0, items: [], has_more: false});
  assert.equal($('#tasks-pagination').hidden, true);
  assert.equal($('#tasks-prev').disabled, true);
  assert.equal($('#tasks-next').disabled, true);
});
