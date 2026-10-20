"""在原位置读取 Word 原生公式；不把分数、上下标展平成无意义的文本。"""

from docx.oxml.ns import qn
from omml2latex import convert_omml

from common.errors import BusinessError

# 固定的转换器支持的表达式和参数容器。属性节点只影响排版，不参与正文。
_MATH_ELEMENTS = frozenset(
    "oMath oMathPara acc bar box borderBox d eqArr f func groupChr limLow limUpp "
    "m nary phant rad sPre sSub sSubSup sSup r t e sub sup num den deg fName lim mr".split()
)
_MATH_NAMESPACE = qn("m:oMath").rsplit("}", 1)[0] + "}"
_WORD_LAYOUT = {
    qn(f"w:{name}")
    for name in ("lastRenderedPageBreak", "br", "bookmarkStart", "bookmarkEnd", "proofErr")
}


def _validate_math(node):
    name = node.tag.rsplit("}", 1)[-1]
    if name.endswith("Pr") or node.tag in _WORD_LAYOUT:
        return
    if not node.tag.startswith(_MATH_NAMESPACE) or name not in _MATH_ELEMENTS:
        raise BusinessError(
            "docx_formula_unsupported",
            "Word 公式包含本地解析不支持的结构，请启用 MinerU 后重新导入原件",
        )
    for child in node:
        _validate_math(child)


def paragraph_text(paragraph):
    """保持文字、超链接、制表符、换行和公式的先后顺序；无公式时沿用原行为。"""
    if not paragraph._p.xpath(".//m:oMath | .//m:oMathPara"):
        return paragraph.text

    def read(node):
        if node.tag in {qn("m:oMath"), qn("m:oMathPara")}:
            _validate_math(node)
            try:
                formula = convert_omml(node)
                if not formula.strip("$ \t\r\n"):
                    raise ValueError("empty formula")
            except (ValueError, TypeError, KeyError, IndexError) as error:
                raise BusinessError(
                    "docx_formula_unsupported",
                    "Word 公式转换失败，请启用 MinerU 后重新导入原件",
                ) from error
            return f"\n\n{formula}\n\n" if node.tag == qn("m:oMathPara") else formula
        if node.tag == qn("w:t"):
            return node.text or ""
        if node.tag == qn("w:tab"):
            return "\t"
        if node.tag in {qn("w:br"), qn("w:cr")}:
            return "\n"
        if node.tag in {qn("w:del"), qn("w:drawing"), qn("w:pict")}:
            return ""
        return "".join(read(child) for child in node)

    return read(paragraph._p)
