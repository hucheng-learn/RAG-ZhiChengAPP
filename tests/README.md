# 测试目录说明

这些文件用于开发回归：修改代码后，确认已有功能仍按预期工作。

| 路径 | 用途 |
| --- | --- |
| `component_tests/`（原 `unit/`） | 组件测试：解析、切片、配置、日志、图片引用识别、语音识别、MinerU 适配、应用工厂、基础服务适配和源码打包；少数也使用应用客户端，因此不称为严格的纯单元测试。 |
| `api_workflow_tests/`（原 `integration/`） | 接口流程测试：通过 HTTP 测试客户端串起上传、问答、目录启用、取消、删除及管理 / 学习页面交互等业务流程。外部服务仍使用替身。 |
| `sample_data/`（原 `fixtures/`） | 可手工导入的示例数据，目前是 `python_questions.md` 题库，不是 pytest fixture 函数。 |
| `conftest.py` | pytest 自动发现的公共配置；`system` fixture 为每个用例提供独立的客户端与运行时。 |
| `fakes.py` | 测试替身：内存数据库之外的对象存储、向量、MinerU、语音、模型及 tokenizer 模拟实现。 |
| `__init__.py` | 将 tests 标记为 Python 包，支持测试之间导入共享工具。 |
| `__pycache__/` | Python 自动生成的字节码缓存，不是测试分类，不需要手动维护，也不进入源码交付包。 |

每个 `test_*.py` 文件开头都有中文用途说明；测试函数名描述要验证的行为。
测试中 `system`、`monkeypatch`、`tmp_path` 等参数由 pytest 自动注入。

在项目根目录的 PowerShell 中执行：

```powershell
# 安装开发工具与 CUDA 测试需要的本地模型库；不需要真实模型权重。
uv sync --extra local-models
uv run --extra local-models pytest -q

# 只运行一个分类，或一个文件。
uv run --extra local-models pytest tests/component_tests -q
uv run --extra local-models pytest tests/api_workflow_tests -q
uv run --extra local-models pytest tests/component_tests/test_parsers.py -q
```

这些测试不验证真实 MongoDB、Milvus、MinIO、MinerU、语音识别、大模型服务或 GPU 的运行能力。
`test_cuda_config.py` 需要安装 torch 和 FlagEmbedding，但会模拟 CUDA 不可用，
无需连接真实 GPU。真实环境可另用 `scripts.doctor` 和 `scripts.check_models` 检查。

## 前端测试

页面逻辑使用 Node.js 内置测试运行器，无需浏览器、真实麦克风或云服务密钥：

```powershell
node --test tests/frontend_tests/speech.test.js
node --test tests/frontend_tests/answer-images.test.js
node --test tests/frontend_tests/manage-pages.test.js
node --test tests/frontend_tests/learning-pages.test.js
```

- `speech.test.js`：覆盖 WAV 编码、录音结束、权限拒绝、取消竞态和草稿保留。
- `answer-images.test.js`：覆盖回答中图片占位的渲染与错误处理。
- `manage-pages.test.js`、`learning-pages.test.js`：覆盖管理页与学习页的渲染和交互状态。
