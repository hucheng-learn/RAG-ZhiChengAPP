# 知成 ZhiChengApp

## 项目概述

知成是面向 IT 职业培训的知识库与 AI 助教。将课程讲义、项目资料和题库导入后，学员可以围绕资料提问、查看回答依据并进行练习。

当前版本适合本机单用户使用，暂不支持登录和用户权限管理。

## 核心功能

- **课程与资料管理**：创建课程，批量上传文件或整个资料文件夹，自动关联 Markdown 图片，查看导入进度，更新或删除资料。
- **课程目录识别**：导入 Markdown、Word、PDF 等课程目录，自动识别系列与课程，支持核对后启用。
- **资料问答**：按课程查找相关内容，逐步显示回答，并提供原文引用和原件下载。
- **题库练习**：导入模板题库或自动识别 Word / PDF 等题库，练习时按需查看标准答案与解析。
- **历史会话**：保存、继续和删除对话。

## 操作流程

1. 打开“资料管理”，创建课程，或选择已有课程。
2. 点击“添加教学资料”，上传 Markdown、TXT、Word、PDF、PPT 或图片，也可选择整个文件夹；单文件最大 200 MB，每批最多 200 个文件、合计最大 200 MB，同一批文件归入同一课程。
3. 在“最近的任务”查看处理进度，导入完成后即可用于问答；失败任务可以重试。
4. 回到问答页面，选择课程并提问，展开引用查看原文。
5. 需要练习时进入题库，选择题目作答，再查看标准答案与解析。

批量导入课程目录或题库时，在上传区域展开“资料设置”，选择对应类型。目录识别后可在“课程目录识别结果”中核对并启用；题库支持 [示例格式](tests/sample_data/python_questions.md) 快速解析；其他排版调用大模型提取原文中的题干和答案，缺少答案或无法核对的字段会报错，识别结果仍标为未审核。

## 聊天语音输入

在 `.env` 中添加 `ZHICHENGAPP_ASR_API_KEY=你的百炼密钥` 并重启应用，默认使用北京地域的 `qwen-audio-3.1-asr-flash`。
如使用其他地域或业务空间专属域名，同时设置 `ZHICHENGAPP_ASR_URL` 为该空间的完整接口地址，密钥必须与地域匹配。
模型和超时可通过 `ZHICHENGAPP_ASR_MODEL`、`ZHICHENGAPP_ASR_TIMEOUT_SECONDS` 配置，详见 `.env.example`。

在聊天输入框点击“语音输入”，允许麦克风权限；点击“停止并识别”后，文字会追加到草稿，核对后点击发送。
每次最多录音 2 分钟，可随时取消；音频经应用后端发送至阿里云识别，不写入课程库或服务器音频文件。
浏览器需支持 Web Audio / AudioWorklet，并通过 `localhost`、`127.0.0.1` 或 HTTPS 访问；局域网 HTTP 地址不能使用麦克风。
接口采用 [阿里云官方语音识别协议](https://www.alibabacloud.com/help/zh/model-studio/fun-asr-flash-recorded-speech-recognition-http-api)。

## 启动命令

需要 **Windows 64 位、Python 3.12、uv 和 Docker Desktop（使用 Linux 容器）**。先启动 Docker Desktop，以下命令均在项目根目录的 PowerShell 中执行。

完整问答还需要：本地 BGE-M3 模型、兼容 FlagReranker 的本地重排模型，以及兼容 OpenAI 接口的大模型服务。模型文件需自行准备。

### 首次启动

安装依赖并生成本地服务配置：

```powershell
uv sync --extra local-models
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
uv run --extra local-models python -m scripts.configure_local
```

然后编辑根目录的 `.env`，填写以下配置：

| 配置项 | 填写内容 |
| --- | --- |
| `ZHICHENGAPP_LLM_BASE_URL` | 大模型服务的 API 基础地址 |
| `ZHICHENGAPP_LLM_API_KEY` | 大模型服务密钥 |
| `ZHICHENGAPP_LLM_MODEL` | 大模型名称 |
| `ZHICHENGAPP_EMBEDDING_MODEL_PATH` | BGE-M3 模型目录，例如 `D:/models/bge-m3` |
| `ZHICHENGAPP_RERANKER_MODEL_PATH` | 重排模型目录 |
| `ZHICHENGAPP_MODEL_DEVICE` | 默认 `cuda:0`，使用 CPU 时改为 `cpu` |

启动基础服务和应用：

```powershell
docker compose --env-file .env -f deploy/compose.yml up -d --wait
uv run --extra local-models uvicorn app:app --host 127.0.0.1 --port 8010 --workers 1
```

打开 [知成页面](http://127.0.0.1:8010) 即可使用。启动或问答异常时，可查看 [服务状态](http://127.0.0.1:8010/health/ready) 中的具体原因。

### 日常启动

配置完成后，每次只需启动 Docker Desktop，再执行：

```powershell
docker compose --env-file .env -f deploy/compose.yml up -d --wait
uv run --extra local-models uvicorn app:app --host 127.0.0.1 --port 8010 --workers 1
```

停止应用按 `Ctrl+C`，停止基础服务执行：

```powershell
docker compose --env-file .env -f deploy/compose.yml stop
```


## 多格式解析与 MinerU

- Markdown / TXT 直接本地读取。未配置 MinerU 时，DOCX 使用本地解析并保留内嵌图片；图片文字不参与检索，页面会显示警告。
- 配置 `ZHICHENGAPP_MINERU_API_TOKEN` 后，DOCX、PDF、DOC、PPT/PPTX 和 PNG/JPG/JPEG/JP2/WEBP/GIF/BMP 使用 MinerU v4；Markdown 关联图片也进行 OCR。默认地址 `https://mineru.net/api/v4`，模型 `vlm`。
- 可复用旧项目 `.env` 的 `MINERU_API_TOKEN`、`MINERU_BASE_URL`，在新项目中分别填写 `ZHICHENGAPP_MINERU_API_TOKEN`、`ZHICHENGAPP_MINERU_BASE_URL`。配置后需重启应用。原件和解析图片会发送给所配置的 MinerU 服务；仅接入接口，无需在应用环境安装 MinerU 模型。
- MinerU 官方接口另有页数和额度限制，200 MB 文件也可能因超过页数或额度而解析失败。解析超时默认 30 分钟，可从已有原件重试；上传超时 10 分钟。
- 单文件、单批总量和解压结果默认均为 200 MiB（209715200 字节），可通过 `MAX_UPLOAD_BYTES`、`MAX_BATCH_BYTES`、`MAX_DOCX_UNPACKED_BYTES` 配置（均加 `ZHICHENGAPP_` 前缀）。文档含大量图片时，压缩文件未超限仍可能触发解压限制。
- 文件夹保留来源相对路径；同名不同目录分别入库。Markdown 图片链接按相对路径匹配，引用式图片也可关联；外部图片不自动抓取，未上传的图片会产生警告。被引用图片作为附件，不额外创建独立资料；不支持的文件单独显示跳过。
- 解析后的 Markdown 行号与原件页码不同，引用会明确标注为“解析后文本”；原文件保留并可下载核对。Word 本地解析仍使用段落 / 表格块定位。
- 上传进入持久化任务队列，后台逐份处理；内存队列满不会拒绝已保存任务。文件夹接口逐文件返回成功或失败，一份文件格式有误不会阻止其余文件。

参考：[MinerU v4 官方接口文档](https://mineru.net/doc/docs/index_en/)。

MinerU API 请求沿用系统代理，结果 CDN 默认直连（沿用旧项目做法）；如网络要求代理下载，可设置 `ZHICHENGAPP_MINERU_DOWNLOAD_TRUST_ENV=true`。
