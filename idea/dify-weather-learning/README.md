# Dify 天气插件：学习版

这是一个最小 Dify Tool Plugin。它使用无需 API Key 的 Open-Meteo，理解诸如“上海明天天气”或“北京 2026-08-08 天气”的输入，返回温湿度、6 小时天气、穿衣建议和心情指数。

## 文件如何对应 Dify 概念

- `manifest.yaml`：插件身份证、运行时和最低资源限制。
- `provider/weather.yaml`：工具供应商在 Dify 插件页显示的信息，以及它包含哪些工具。
- `tools/get_current_weather.yaml`：告诉 Dify/LLM 工具叫什么、接收什么参数。
- `tools/get_current_weather.py`：实际 Skill 逻辑；先把城市转经纬度，再查询天气。
- `main.py`：启动 Dify Plugin SDK；SDK 按 `manifest.yaml` 自动发现并注册 Provider。

## 本地隔离安装

在本目录运行以下命令。`.venv`、CLI 二进制和最终 `.difypkg` 都保持在本目录内：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

然后从 Dify 官方 Plugin Daemon 的 Releases 下载与 Mac 芯片匹配的 `dify-plugin-darwin-arm64`（Apple 芯片）或 `dify-plugin-darwin-amd64`（Intel），保存为本目录的 `dify`，并执行：

```bash
chmod +x ./dify
./dify version
```

## 在 Dify 中调试并安装

1. 在 Dify 工作区打开 **Plugins → Debug Plugin**，复制服务地址与 Debug Key。
2. 复制 `.env.example` 为 `.env`，填入上述两个值；不要把 `.env` 提交或分享。
3. 保持虚拟环境激活，运行 `python -m main`。
4. 回到 Dify 的插件页，看到“天气查询（学习版）”后安装并启用它。
5. 新建 Workflow，添加 **Tool** 节点，选择该工具；输入 `query=上海明天天气` 测试。也可在 Agent 应用中将它启用，让模型传入完整的天气请求。

## 打包成可上传插件

结束调试后，在本目录运行：

```bash
./dify plugin package .
```

生成的 `.difypkg` 可在 Dify 的 **Plugins → Install from local file** 上传。若只为本地学习，不需要发布到 Marketplace。

## 删除

停止 `python -m main` 后，删除整个 `dify-weather-learning/` 目录即可清除这个练习的源代码、虚拟环境、CLI 和打包文件。
