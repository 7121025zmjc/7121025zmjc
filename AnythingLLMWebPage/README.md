# AnythingLLM WebPage

一个 Flask 网页，把本地文件上传到 AnythingLLM 指定的工作区并完成向量化嵌入。给人用的界面，不需要 LLM 客户端。

## 功能与界面状态

- 打开页面先拉一次 `/api/v1/workspaces`，把工作区做成下拉框，**列表来自接口，不是写死**
- 提交后前端立刻进入加载态：按钮禁用、文案变"上传中…"、显示转圈提示
- 成功态：绿色提示条，显示"已上传 xxx 到工作区 yyy 并完成向量化嵌入"
- 失败态：红色提示条，显示可读原因（连接失败、401、超时、格式不支持、体积超限等），不会再抛 500 堆栈页

完整流程：`选择文件 → /api/v1/document/upload → 取 location → /api/v1/workspace/{slug}/update-embeddings`。

## 目录结构

```
AnythingLLMWebPage/
├── app.py                  # Flask 应用：配置加载、API 调用、错误收敛
├── templates/index.html    # 单页界面，含加载/成功/失败三态
├── config.ini.example      # 配置模板
└── requirements.txt        # flask、requests
```

## 配置

```bash
copy config.ini.example config.ini
```

```ini
[ANYTHINGLLM]
api_key = your_api_key_here
base_url = http://localhost:3001
```

环境变量优先：`ANYTHINGLLM_API_KEY`、`ANYTHINGLLM_BASE_URL`。

**密钥只在服务端读取**，页面里没有任何 key，浏览器抓包也看不到。

## 启动

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python app.py
```

浏览器打开 `http://127.0.0.1:5000`。启动时会先探一次上游，连不上会打印警告但仍然起服务，方便先看界面。

## 限制

| 项 | 值 | 原因 |
| --- | --- | --- |
| 单文件体积 | 30 MB | AnythingLLM 解析大文件容易超时，同时避免整文件读入内存 |
| 允许扩展名 | txt / md / pdf / doc / docx / csv / json / html / pptx / xlsx | 其余格式解析器不可用 |
| 请求超时 | 列表 30 秒，上传与嵌入各 180 秒 | 本地模型嵌入慢 |

## 验证

1. 页面能正常渲染，工作区下拉框里有 AnythingLLM 里真实存在的工作区。
2. 上传一个 txt，页面出现绿色"已上传…并完成向量化"提示。
3. 到 AnythingLLM 里打开该工作区，能看到这个文档且状态为已完成嵌入。
4. 故意填错 `base_url` 重启，页面显示红色"无法连接 AnythingLLM"而不是报错页。

## 失败场景

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 页面提示"无法连接 AnythingLLM" | 上游没启动 / 地址错 | 启动 AnythingLLM，核对 `base_url` |
| 页面提示"HTTP 401" | key 无效 | 重新生成 API Key |
| 提示"暂不支持 xyz 格式" | 扩展名不在白名单 | 转成 md/txt 再上传 |
| 提示"文件超过 30MB" | 体积超限 | 压缩或拆分 |
| 下拉框为空并报错 | AnythingLLM 里没有工作区 | 先创建一个工作区 |

## 安全

- 密钥从 `config.ini` 读取，不入库、不进前端
- 文件名经 Jinja 自动转义后渲染，避免把用户输入当 HTML 执行
- 上传内容先做体积校验，再交给上游，避免一次把超大文件读进内存
