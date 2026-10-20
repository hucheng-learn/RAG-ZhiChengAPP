// 仅将引用证据中的图片转为同源附件请求；其余内容始终作为文本。
(function (root) {
  const pattern = /```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|(`+)[^\n]*?\1|!\[([^\]\n]*)\]\(<?([^\s)>]+)>?(?:\s+["'][^"']*["'])?\)/g;
  function images(text) {
    return [...String(text || '').matchAll(new RegExp(pattern))]
      .filter(match => match[3]).map(match => match[3]);
  }
  function render(container, text, citations = []) {
    text = String(text || '');
    container.replaceChildren();
    let offset = 0;
    for (const match of text.matchAll(new RegExp(pattern))) {
      if (!match[3]) continue;
      container.append(document.createTextNode(text.slice(offset, match.index)));
      const path = match[3];
      let sources = citations.filter(item => item.source_available !== false &&
        item.document_id && item.version_id && images(item.text).includes(path));
      const nearby = text.slice(match.index + match[0].length).match(/^\s*\[(\d+)\]/);
      if (nearby) sources = sources.filter(item => String(item.id) === nearby[1]);
      const unique = new Set(sources.map(item => `${item.document_id}/${item.version_id}`));
      if (sources.length && unique.size === 1) {
        const source = sources[0], image = document.createElement('img');
        image.className = 'document-image';
        image.alt = match[2] || '资料图片';
        image.loading = 'lazy';
        image.src = `/api/v1/documents/${encodeURIComponent(source.document_id)}/asset?version_id=${encodeURIComponent(source.version_id)}&path=${encodeURIComponent(path)}`;
        image.onerror = () => {
          const fallback = document.createElement('span');
          fallback.textContent = `图片加载失败：${image.alt}（可展开引用或下载原件查看）`;
          image.replaceWith(fallback);
        };
        container.append(image);
      } else {
        const fallback = document.createElement('span');
        fallback.textContent = `图片无法匹配引用资料：${match[2] || path}`;
        container.append(fallback);
      }
      offset = match.index + match[0].length;
    }
    container.append(document.createTextNode(text.slice(offset)));
  }
  root.AnswerImages = {render};
})(globalThis);
