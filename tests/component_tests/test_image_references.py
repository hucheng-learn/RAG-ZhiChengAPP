from utils.parsers.files import append_image_text, image_references, resolve_image


def test_html_images_include_inline_blocks_and_decode_entities():
    source = '''# 架构
<img src="images/1.整体架构图.jpg" style="zoom: 67%;" />

正文 <IMG SRC='images/a&amp;b.png'> ![同图](images/block.png)

<div>
<img src=images/block.png>
</div>
'''
    assert image_references(source) == [
        "images/1.整体架构图.jpg", "images/a&b.png", "images/block.png"
    ]
    assert resolve_image("掌柜智库/01.md", image_references(source)[0]) == (
        "掌柜智库/images/1.整体架构图.jpg"
    )


def test_image_code_examples_and_comments_are_ignored():
    source = '''`<img src="inline.png">`

```html
<img src="fenced.png">
```

    <img src="indented.png">

<!-- <img src="comment.png"> -->

<pre><img src="pre.png"></pre>

<code><img src="code.png"></code>

<script>const example = '<img src="script.png">';</script>
'''
    assert image_references(source) == []


def test_html_image_ocr_is_inserted_before_next_heading():
    source = '# 第一节\n<img src="a.jpg" />\n\n# 第二节\n正文'
    result = append_image_text(source, {"a.jpg": "架构说明"})
    assert result.index("架构说明") < result.index("# 第二节")
    assert '<img src="a.jpg" />' in result
