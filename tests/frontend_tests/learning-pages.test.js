const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function setup(api) {
  const elements = new Map();
  function node(tag, text) {
    return {tag, textContent: text, children: [], hidden: false, disabled: false,
      append(...items) { this.children.push(...items); },
      replaceChildren(...items) { this.children = items; }};
  }
  const $ = selector => {
    if (!elements.has(selector)) elements.set(selector, {...node('div'), value: ''});
    return elements.get(selector);
  };
  const context = vm.createContext({$, node, api, URLSearchParams,
    questionCard: row => node('article', row.question_id),
    state: {}, syncCourse() {}, notice(message) { throw Error(message); }});
  const source = fs.readFileSync(path.join(__dirname, '../../page/app.js'), 'utf8');
  vm.runInContext(source.slice(source.indexOf('const learningPageSize=10;'),
    source.indexOf('async function loadSessions()')), context);
  return {$, context};
}

test('project files form one chapter; chapter pages contain ten groups', async () => {
  const rows = [];
  for (let chapter = 0; chapter < 11; chapter++) {
    for (let file = 0; file < 2; file++) rows.push({
      id: `${chapter}-${file}`, document_id: `${chapter}-${file}`,
      source_name: `${chapter}-${file}.md`, title: '内部小节',
      chapter_id: `project-${chapter}`, chapter_title: `项目 ${chapter}`,
    });
  }
  const {$, context} = setup(async () => ({items: rows}));
  $('#course').value = 'course';
  await context.loadSections();
  assert.equal($('#sections').children.length, 10);
  assert.equal($('#sections').children[0].children[0].textContent, '项目 0 · 2 份资料');
  assert.equal($('#sections').children[0].children.length, 3);
  assert.equal($('#sections-prev').disabled, true);
  $('#sections-next').onclick();
  assert.equal($('#sections').children.length, 1);
  assert.equal($('#sections-next').disabled, true);
  $('#sections-prev').onclick();
  assert.equal($('#sections').children.length, 10);
});

test('question pages preserve filters; searching and course changes reset the page', async () => {
  const calls = [];
  const {$} = setup(async url => {
    const params = new URL(url, 'http://test').searchParams;
    calls.push(params);
    return {items: [{question_id: params.get('offset')}], has_more: params.get('offset') === '0'};
  });
  $('#course').value = 'python'; $('#question-code').value = ' Q1 ';
  await $('#search-questions').onclick();
  assert.equal(calls[0].get('limit'), '10');
  assert.equal(calls[0].get('offset'), '0');
  assert.equal(calls[0].get('code'), 'Q1');
  await $('#questions-next').onclick();
  assert.equal(calls[1].get('offset'), '10');
  assert.equal(calls[1].get('course_id'), 'python');
  assert.equal($('#questions-next').disabled, true);
  await $('#search-questions').onclick();
  assert.equal(calls[2].get('offset'), '0');
  $('#course').value = ''; $('#course').onchange();
  assert.equal($('#questions').children.length, 0);
  assert.equal($('#questions-pagination').hidden, true);
});

test('a stale question response cannot overwrite a new course', async () => {
  let resolve;
  const {$} = setup(() => new Promise(done => {resolve = done;}));
  $('#course').value = 'python';
  const pending = $('#search-questions').onclick();
  $('#course').value = ''; $('#course').onchange();
  resolve({items: [{question_id: 'stale'}], has_more: false});
  await pending;
  assert.equal($('#questions').children.length, 0);
  assert.equal($('#questions-pagination').hidden, true);
});
