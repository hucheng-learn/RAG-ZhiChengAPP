const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function setup() {
  function element(tag) {
    return {tag, children: [], append(value) {this.children.push(value);},
      replaceChildren() {this.children = [];}, replaceWith(value) {this.replacement = value;}};
  }
  const context = vm.createContext({document: {
    createElement: element, createTextNode: text => ({text})
  }});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../page/answer-images.js'), 'utf8'), context);
  return {render: context.AnswerImages.render, container: element('div')};
}
const source = {id: 1, document_id: 'doc', version_id: 'v1',
  text: '![架构](images/arch.jpg)'};

test('answer image uses the cited document asset endpoint and has a visible error fallback', () => {
  const {render, container} = setup();
  render(container, '架构如下：\n![架构](images/arch.jpg) [1]', [source]);
  const image = container.children.find(child => child.tag === 'img');
  assert.equal(image.src, '/api/v1/documents/doc/asset?version_id=v1&path=images%2Farch.jpg');
  assert.equal(image.alt, '架构');
  image.onerror();
  assert.match(image.replacement.textContent, /图片加载失败/);
});

test('unverified paths, deleted sources, HTML, and code never become active content', () => {
  const {render, container} = setup();
  for (const text of ['![x](https://example.com/x.jpg)', '![x](images/missing.jpg)',
    '`![x](images/arch.jpg)`', '```md\n![x](images/arch.jpg)\n```', '<img src=x onerror=alert(1)>']) {
    render(container, text, [source]);
    assert.equal(container.children.some(child => child.tag === 'img'), false);
  }
  render(container, source.text, [{...source, source_available: false}]);
  assert.equal(container.children.some(child => child.tag === 'img'), false);
});

test('duplicate paths across documents require the adjacent citation to select a source', () => {
  const {render, container} = setup();
  const sources = [source, {...source, id: 2, document_id: 'other'}];
  render(container, source.text, sources);
  assert.equal(container.children.some(child => child.tag === 'img'), false);
  render(container, source.text + ' [2]', sources);
  assert.match(container.children.find(child => child.tag === 'img').src, /documents\/other\/asset/);
});
